import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Mic, MicOff, Settings2, Trash2, X } from 'lucide-react';
import { ArcReactor, type ReactorPhase } from '../components/Jarvis/ArcReactor';
import { ChatPanel } from '../components/Jarvis/ChatPanel';
import { SettingsPanel } from '../components/Jarvis/SettingsPanel';
import { VoiceVisualizer } from '../components/Jarvis/VoiceVisualizer';
import { chat, clearHistory, friendlyError, getHealth, getHistory, getSettings, isDesktop, transcribeAudio, type ChatMessage, type Health } from '../lib/jarvis/api';
import { JarvisVoice, speechSupported, type VoiceState } from '../lib/jarvis/voice';

export function describePhase(phase: ReactorPhase, options: { wakeWord?: string; awake?: boolean; requireWakeWord?: boolean; calibrating?: boolean; connecting?: boolean } = {}): string {
  if (phase === 'offline') return options.connecting ? 'Connecting to JARVIS core…' : 'JARVIS core offline.';
  if (phase === 'thinking') return 'Thinking…';
  if (phase === 'speaking') return 'Speaking…';
  if (phase === 'hearing') return 'Listening…';
  if (phase === 'listening') return options.calibrating ? 'Calibrating to the room. A second of quiet, please…' : options.awake || options.requireWakeWord === false ? "I'm listening." : `Say “${(options.wakeWord ?? 'jarvis').charAt(0).toUpperCase()}${(options.wakeWord ?? 'jarvis').slice(1)}” to wake me.`;
  return 'Standing by. Activate the microphone, or type below.';
}

export function JarvisPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [draft, setDraft] = useState('');
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  const levelRef = useRef(0);
  const voiceRef = useRef<JarvisVoice | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (isDesktop()) {
      void getHealth().then(setHealth).catch(() => {});
      void getHistory().then(setMessages).catch(() => {});
    }
    const timer = window.setInterval(() => {
      if (isDesktop()) void getHealth().then(setHealth).catch(() => {});
    }, 12000);
    return () => window.clearInterval(timer);
  }, []);

  const send = useCallback(async (text: string) => {
    const clean = text.trim();
    if (!clean || busyRef.current) return;
    busyRef.current = true;
    setDraft('');
    setInput('');
    setError('');
    setMessages((items) => [...items, { role: 'user', text: clean }]);
    setThinking(true);
    setVoiceState('thinking');
    try {
      const reply = await chat(clean);
      setMessages((items) => [...items, { role: 'assistant', text: reply.text }]);
      const settings = await import('../lib/jarvis/api').then((m) => m.getSettings()).catch(() => null);
      if (settings?.voice && voiceRef.current) await voiceRef.current.speak(reply.text);
    } catch (e) {
      const message = friendlyError(e);
      setError(message);
      setMessages((items) => [...items, { role: 'assistant', text: message }]);
      if (voiceRef.current) await voiceRef.current.speak(message);
    } finally {
      setThinking(false);
      busyRef.current = false;
      setVoiceState(voiceRef.current?.isRunning ? 'listening' : 'idle');
    }
  }, []);

  const stopVoice = useCallback(() => {
    voiceRef.current?.stop();
    voiceRef.current = null;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current = null;
    setDraft('');
    setVoiceState('idle');
  }, []);

  const startVoice = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone permission is unavailable. Check Windows microphone privacy settings.');
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError('Microphone access was denied. Allow microphone access in Windows Settings, then try again.');
      return;
    }
    setError('');
    const settings = await getSettings().catch(() => null);
    if (!speechSupported()) {
      // WebView2 builds without SpeechRecognition can still hear voice: record
      // a short push-to-talk clip and let the selected Gemini model transcribe it.
      const fallbackVoice = new JarvisVoice({
        requireWakeWord: settings?.wake_word ?? true,
        onTranscript: () => {},
        onState: setVoiceState,
        onError: setError,
      });
      voiceRef.current = fallbackVoice;
      if (typeof MediaRecorder === 'undefined') {
        stream.getTracks().forEach((track) => track.stop());
        setError('This Windows WebView cannot record audio. Update Microsoft Edge WebView2 and check microphone privacy settings.');
        return;
      }
      try {
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
        const recorder = new MediaRecorder(stream, { mimeType });
        const chunks: BlobPart[] = [];
        recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
        recorder.onstop = async () => {
          recorderRef.current = null;
          stream.getTracks().forEach((track) => track.stop());
          recordingStreamRef.current = null;
          const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
          if (blob.size < 500) {
            setVoiceState('idle');
            setError('I didn’t hear any speech. Tap the microphone, speak clearly, then tap it again.');
            return;
          }
          setVoiceState('thinking');
          setDraft('Transcribing your voice…');
          try {
            const dataUrl = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result));
              reader.onerror = () => reject(new Error('Could not read the audio recording.'));
              reader.readAsDataURL(blob);
            });
            const base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
            const transcript = await transcribeAudio(base64, blob.type || 'audio/webm');
            setDraft('');
            if (transcript.trim()) await send(transcript);
            else setError('I couldn’t make out the words. Please try speaking a little closer to the microphone.');
          } catch (e) {
            setDraft('');
            setError(friendlyError(e));
            setVoiceState('idle');
          }
        };
        recorder.start();
        recorderRef.current = recorder;
        recordingStreamRef.current = stream;
        setDraft('Recording — tap the microphone when you’re done');
        setVoiceState('hearing');
        window.setTimeout(() => {
          if (recorderRef.current === recorder && recorder.state !== 'inactive') recorder.stop();
        }, 15000);
      } catch (e) {
        stream.getTracks().forEach((track) => track.stop());
        setError(`Could not start audio recording: ${friendlyError(e)}`);
      }
      return;
    }
    stream.getTracks().forEach((track) => track.stop());
    const voice = new JarvisVoice({
      requireWakeWord: settings?.wake_word ?? true,
      onTranscript: (text, isFinal) => {
        setDraft(text);
        if (isFinal && text.trim()) void send(text);
      },
      onState: setVoiceState,
      onError: setError,
    });
    voiceRef.current = voice;
    JarvisVoice.warmUpVoices();
    voice.startListening();
  }, [send]);

  useEffect(() => () => {
    voiceRef.current?.stop();
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  const wipe = async () => {
    try {
      await clearHistory();
      setMessages([]);
      setError('');
    } catch (e) {
      setError(friendlyError(e));
    }
  };

  return (
    <main className="jarvis-app">
      <div className="jarvis-shell">
        <header className="jarvis-header">
          <div className="flex items-center gap-3">
            <div className="jarvis-mark">J</div>
            <div>
              <div className="jarvis-title">JARVIS</div>
              <div className="jarvis-subtitle">PERSONAL AI SYSTEM</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className={`jarvis-status ${health?.ready ? 'ready' : ''}`}>
              <span className="status-light" />
              {health?.ready ? `${health.engine.toUpperCase()} ONLINE` : 'KEY NEEDED'}
            </div>
            <button className="icon-button" type="button" title="Clear conversation" onClick={() => void wipe()}><Trash2 size={16} /></button>
            <button className="icon-button" type="button" title="Settings" onClick={() => setSettingsOpen(true)}><Settings2 size={17} /></button>
          </div>
        </header>

        {settingsOpen ? (
          <div className="jarvis-content"><SettingsPanel onClose={() => setSettingsOpen(false)} /></div>
        ) : (
          <>
            <section className="jarvis-hero">
              <ArcReactor state={voiceState} level={levelRef.current} onClick={() => voiceState === 'listening' || voiceState === 'hearing' ? stopVoice() : void startVoice()} />
              <div className="mt-10 w-full max-w-lg">
                <VoiceVisualizer state={voiceState} levelRef={levelRef} />
              </div>
              <div className="mt-2 text-[10px] tracking-[0.3em] uppercase" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                {health?.engine === 'ollama' ? `LOCAL · ${health.model}` : `CLOUD · ${health?.model ?? 'GEMINI'}`}
              </div>
            </section>
            <ChatPanel messages={messages} draft={draft} thinking={thinking} />
            <footer className="jarvis-footer">
              {error && <div className="jarvis-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError('')} aria-label="Dismiss"><X size={14} /></button></div>}
              <form className="jarvis-compose" onSubmit={submit}>
                <button
                  type="button"
                  className={`mic-button ${voiceState !== 'idle' ? 'active' : ''}`}
                  title={voiceState === 'idle' ? 'Start voice listening' : 'Stop listening'}
                  onClick={() => voiceState === 'listening' || voiceState === 'hearing' ? stopVoice() : void startVoice()}
                >{voiceState === 'idle' ? <Mic size={19} /> : <MicOff size={19} />}</button>
                <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask JARVIS anything…" aria-label="Message JARVIS" />
                <button className="send-button" type="submit" disabled={!input.trim() || thinking} aria-label="Send"><ArrowUp size={18} /></button>
              </form>
              <div className="jarvis-footnote">CTRL + ALT + J TO SUMMON · YOUR DATA STAYS ON THIS PC</div>
            </footer>
          </>
        )}
      </div>
    </main>
  );
}
