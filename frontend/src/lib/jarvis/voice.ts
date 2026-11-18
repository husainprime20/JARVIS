/**
 * JARVIS voice: wake word, speech-to-text and text-to-speech.
 *
 * Uses the browser's Web Speech API (available in the Windows WebView2), so
 * there are no voice models to download and nothing heavy to run. JARVIS
 * listens for its name, transcribes what you say, and answers in a British
 * voice when one is available.
 */

export type VoiceState =
  | 'idle'
  | 'listening' // waiting for the wake word
  | 'hearing' // transcribing you
  | 'thinking'
  | 'speaking';

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
    length: number;
  }>;
}

type RecognitionCtor = new () => SpeechRecognitionLike;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as RecognitionCtor | null;
}

export function speechSupported(): boolean {
  return recognitionCtor() !== null && 'speechSynthesis' in window;
}

/** Pick the most British-sounding voice available, else any English voice. */
function pickVoice(): SpeechSynthesisVoice | null {
  if (!('speechSynthesis' in window)) return null;
  const voices = window.speechSynthesis.getVoices();
  if (voices.length === 0) return null;
  const british = voices.find(
    (v) => v.lang?.toLowerCase().startsWith('en-gb') && /male|daniel|george|arthur|brian/i.test(v.name),
  );
  if (british) return british;
  const gb = voices.find((v) => v.lang?.toLowerCase().startsWith('en-gb'));
  if (gb) return gb;
  const en = voices.find((v) => v.lang?.toLowerCase().startsWith('en'));
  return en ?? voices[0] ?? null;
}

/** Strip markdown and symbols so TTS reads naturally. */
export function cleanForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_`#>\[\]()]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export class JarvisVoice {
  private recognition: SpeechRecognitionLike | null = null;
  private onTranscript: (text: string, isFinal: boolean) => void;
  private onState: (state: VoiceState) => void;
  private onError: (message: string) => void;
  private wakeWord: string;
  private requireWakeWord: boolean;
  private armed = false; // wake word heard, waiting for the command
  private running = false;
  private wantWake = true;
  private armTimer: number | null = null;

  constructor(opts: {
    wakeWord?: string;
    requireWakeWord?: boolean;
    onTranscript: (text: string, isFinal: boolean) => void;
    onState: (state: VoiceState) => void;
    onError: (message: string) => void;
  }) {
    this.wakeWord = (opts.wakeWord ?? 'jarvis').toLowerCase();
    this.requireWakeWord = opts.requireWakeWord ?? true;
    this.onTranscript = opts.onTranscript;
    this.onState = opts.onState;
    this.onError = opts.onError;
  }

  get isRunning(): boolean {
    return this.running;
  }

  /** Begin listening for the wake word. */
  startListening(): void {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      this.onError(
        'Voice input is not available in this browser. JARVIS needs Microsoft Edge WebView2 with speech support.',
      );
      return;
    }
    if (this.running) return;
    this.running = true;
    this.wantWake = true;
    this.spawn(Ctor);
  }

  private spawn(Ctor: RecognitionCtor): void {
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-GB';
    rec.onresult = (event) => this.handleResult(event);
    rec.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        this.onError(
          'Microphone access was blocked. Allow the microphone for JARVIS and try again.',
        );
        this.running = false;
      }
      // 'no-speech' and 'aborted' are normal; onend restarts the recogniser.
    };
    rec.onend = () => {
      if (this.running) {
        // Speech recognition stops on its own; restart it to keep listening.
        try {
          rec.start();
        } catch {
          /* already started */
        }
      }
    };
    this.recognition = rec;
    try {
      rec.start();
      this.onState(this.armed ? 'hearing' : 'listening');
    } catch {
      this.onError('Could not start the microphone.');
      this.running = false;
    }
  }

  private handleResult(event: SpeechRecognitionEventLike): void {
    let interim = '';
    let finalText = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0]?.transcript ?? '';
      if (result.isFinal) finalText += transcript;
      else interim += transcript;
    }
    const heard = (finalText || interim).trim();
    if (!heard) return;

    if (!this.requireWakeWord) {
      this.onTranscript(heard, Boolean(finalText));
      this.onState('hearing');
      return;
    }

    if (!this.armed) {
      // Waiting for the wake word.
      const lower = heard.toLowerCase();
      if (this.containsWakeWord(lower)) {
        this.armed = true;
        this.onState('hearing');
        // Whatever came after the wake word is the start of the command.
        const after = lower
          .slice(lower.indexOf(this.wakeWord) + this.wakeWord.length)
          .replace(/^[\s,]+/, '')
          .trim();
        if (after) {
          this.onTranscript(after, Boolean(finalText));
          if (finalText) {
            this.armed = false;
            this.onState('listening');
            return;
          }
        }
        this.armTimer = window.setTimeout(() => {
          this.armed = false;
          this.onState('listening');
        }, 6000);
      }
      return;
    }

    // Armed: everything is the command.
    this.onTranscript(heard, Boolean(finalText));
    if (finalText) {
      this.armed = false;
      if (this.armTimer) window.clearTimeout(this.armTimer);
      this.onState('listening');
    }
  }

  private containsWakeWord(lower: string): boolean {
    const variants = [this.wakeWord, 'jarvis', 'jervis', 'jarves', 'service'];
    return variants.some((v) => new RegExp(`\\b${v}\\b`).test(lower));
  }

  /** Stop listening and speaking. */
  stop(): void {
    this.running = false;
    this.armed = false;
    if (this.armTimer) window.clearTimeout(this.armTimer);
    try {
      this.recognition?.abort();
    } catch {
      /* ignore */
    }
    this.recognition = null;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    this.onState('idle');
  }

  /** Speak a reply in a British voice. Resolves when finished. */
  speak(text: string): Promise<void> {
    return new Promise((resolve) => {
      if (!('speechSynthesis' in window)) {
        resolve();
        return;
      }
      const clean = cleanForSpeech(text);
      if (!clean) {
        resolve();
        return;
      }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(clean);
      const voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.lang = voice?.lang ?? 'en-GB';
      utterance.rate = 1.0;
      utterance.pitch = 0.9;
      this.onState('speaking');
      utterance.onend = () => {
        this.onState(this.running ? 'listening' : 'idle');
        resolve();
      };
      utterance.onerror = () => {
        this.onState(this.running ? 'listening' : 'idle');
        resolve();
      };
      window.speechSynthesis.speak(utterance);
    });
  }

  /** Warm up the voice list (browsers load voices asynchronously). */
  static warmUpVoices(): void {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.getVoices();
      window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
    }
  }
}
