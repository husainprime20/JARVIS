/**
 * The JARVIS conversation rules, shared by hands-free, push-to-talk and typed
 * input. Mirrors `VoiceLoop.handle` in `openjarvis/cli/listen_cmd.py`.
 */
import { toSpeakableText } from '../message-text';
import {
  WakeWordDetector,
  isCancelCommand,
  isNoiseTranscript,
  isStopCommand,
} from './wakeWord';

export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
}

export type JarvisEvent =
  | { kind: 'you'; text: string }
  | { kind: 'jarvis'; text: string }
  | { kind: 'error'; text: string }
  | { kind: 'overheard'; text: string }
  | { kind: 'phase'; phase: 'thinking' | 'speaking' | 'done' };

export type Outcome = 'ignored' | 'ack' | 'answered' | 'stop' | 'cancel' | 'error';

export interface ConversationOptions {
  ask: (request: string, history: ChatTurn[]) => Promise<string>;
  speak: (text: string) => Promise<void>;
  emit: (event: JarvisEvent) => void;
  detector?: WakeWordDetector;
  honorific?: string;
  followUpMs?: number;
  maxHistory?: number;
  now?: () => number;
}

const MAX_SPOKEN_CHARS = 900;

/** Speakable text, trimmed at a sentence when it is long. */
export function spokenVersion(text: string, limit = MAX_SPOKEN_CHARS): string {
  const clean = toSpeakableText(text);
  if (clean.length <= limit) return clean;
  const cut = clean.slice(0, limit);
  const end = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('? '), cut.lastIndexOf('! '));
  const head = end > limit / 3 ? cut.slice(0, end + 1) : `${cut.trimEnd()}…`;
  return `${head} The rest is on screen.`;
}

export function friendlyError(error: unknown, honorific = 'sir'): string {
  const message = String(error instanceof Error ? error.message : error).toLowerCase();
  const title = honorific ? `, ${honorific}` : '';
  const has = (...markers: string[]) => markers.some((m) => message.includes(m));
  if (has('429', 'resource_exhausted', 'rate limit', 'quota', 'too many')) {
    return `I've reached my usage limit for the moment${title}. Give it a minute, or switch me to another brain.`;
  }
  if (has('401', '403', 'api key', 'api_key', 'unauthorized', 'permission')) {
    return `My API key was rejected${title}. Please check it and restart me.`;
  }
  if (has('failed to fetch', 'networkerror', 'connect', 'timed out', 'timeout', 'unreachable', '502', '503')) {
    return `I can't reach my brain right now${title}. Is the OpenJarvis server running?`;
  }
  return `I'm afraid something went wrong${title}. The details are on screen.`;
}

export function greeting(honorific = 'sir', now: Date = new Date()): string {
  const hour = now.getHours();
  const part = hour >= 5 && hour < 12 ? 'Good morning' : hour >= 12 && hour < 18 ? 'Good afternoon' : 'Good evening';
  const title = honorific ? `, ${honorific}` : '';
  return `${part}${title}. JARVIS online and at your service.`;
}

export class JarvisConversation {
  history: ChatTurn[] = [];
  private readonly options: Required<Omit<ConversationOptions, 'detector'>> & {
    detector: WakeWordDetector;
  };
  private awakeUntil = 0;
  private acks = 0;

  constructor(options: ConversationOptions) {
    this.options = {
      detector: options.detector ?? new WakeWordDetector('jarvis'),
      honorific: options.honorific ?? 'sir',
      followUpMs: options.followUpMs ?? 8000,
      maxHistory: options.maxHistory ?? 12,
      now: options.now ?? (() => Date.now()),
      ask: options.ask,
      speak: options.speak,
      emit: options.emit,
    };
  }

  get honorific(): string {
    return this.options.honorific;
  }

  /** Whether a request is accepted without the wake word right now. */
  isAwake(): boolean {
    return this.options.now() < this.awakeUntil;
  }

  /** Milliseconds left in the follow-up window. */
  awakeRemainingMs(): number {
    return Math.max(0, this.awakeUntil - this.options.now());
  }

  wake(): void {
    if (this.options.followUpMs > 0) this.awakeUntil = this.options.now() + this.options.followUpMs;
  }

  sleep(): void {
    this.awakeUntil = 0;
  }

  private title(): string {
    return this.options.honorific ? `, ${this.options.honorific}` : '';
  }

  private nextAck(): string {
    const options = [`Yes${this.title()}?`, 'At your service.', `I'm listening${this.title()}.`];
    this.acks += 1;
    return options[(this.acks - 1) % options.length];
  }

  private async reply(text: string): Promise<void> {
    this.options.emit({ kind: 'jarvis', text });
    this.options.emit({ kind: 'phase', phase: 'speaking' });
    try {
      await this.options.speak(spokenVersion(text));
    } finally {
      this.options.emit({ kind: 'phase', phase: 'done' });
    }
  }

  /**
   * Process one transcript. `direct` input (typed, or push-to-talk) is always
   * a request; hands-free speech needs the wake word unless JARVIS is awake.
   */
  async handle(transcript: string, { direct = false }: { direct?: boolean } = {}): Promise<Outcome> {
    const text = (transcript ?? '').trim();
    if (!text || (!direct && isNoiseTranscript(text))) return 'ignored';

    const match = this.options.detector.find(text);
    let request: string;
    if (match) request = match.command;
    else if (direct || this.isAwake()) request = text;
    else {
      this.options.emit({ kind: 'overheard', text });
      return 'ignored';
    }

    if (isStopCommand(request || text)) {
      this.sleep();
      await this.reply(`Very good${this.title()}. Signing off.`);
      return 'stop';
    }
    if (!request) {
      this.options.emit({ kind: 'you', text });
      await this.reply(this.nextAck());
      this.wake();
      return 'ack';
    }
    if (isCancelCommand(request)) {
      this.sleep();
      await this.reply(`Very well${this.title()}.`);
      return 'cancel';
    }

    this.options.emit({ kind: 'you', text: request });
    this.options.emit({ kind: 'phase', phase: 'thinking' });
    let answer: string;
    try {
      answer = (await this.options.ask(request, [...this.history])).trim();
    } catch (error) {
      this.options.emit({ kind: 'error', text: error instanceof Error ? error.message : String(error) });
      await this.reply(friendlyError(error, this.options.honorific));
      this.wake();
      return 'error';
    }
    answer = answer || `Done${this.title()}.`;
    this.remember(request, answer);
    await this.reply(answer);
    this.wake();
    return 'answered';
  }

  private remember(request: string, answer: string): void {
    this.history.push({ role: 'user', content: request }, { role: 'assistant', content: answer });
    const extra = this.history.length - this.options.maxHistory;
    if (extra > 0) this.history.splice(0, extra);
  }
}
