import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Hand, Mic, MicOff, SendHorizontal, Square, Volume2, VolumeX } from 'lucide-react';
import { ArcReactor, type ReactorPhase } from '../components/Jarvis/ArcReactor';
import { StatsPanel } from '../components/Jarvis/StatsPanel';
import { useJarvisMic, type Utterance } from '../hooks/useJarvisMic';
import { fetchSpeechHealth, transcribeAudio, type SpeechHealth } from '../lib/api';
import { askJarvis, fetchJarvisStatus, speakAndWait, stopSpeaking, type JarvisStatus } from '../lib/jarvis/api';
import { JarvisConversation, greeting } from '../lib/jarvis/conversation';
import { WakeWordDetector } from '../lib/jarvis/wakeWord';
import { useAppStore } from '../lib/store';
import { useTtsStore } from '../lib/tts';

type LogKind = 'you' | 'jarvis' | 'error';
interface LogEntry {
  id: number;
  kind: LogKind;
  text: string;
  at: number;
}

const STATUS_POLL_MS = 5000;
const MAX_LOG = 60;
const SERVE_COMMAND = 'jarvis serve --agent jarvis';
// One command for every JARVIS dependency: a partial `uv sync` would uninstall the rest.
const INSTALL_COMMAND = 'uv sync --extra jarvis --group desktop-native';

function capitalise(word: string): string {
  return word ? word.charAt(0).toUpperCase() + word.slice(1) : word;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The one-line status under the reactor. */
export function describePhase(
  phase: ReactorPhase,
  {
    wakeWord = 'jarvis',
    awake = false,
    requireWakeWord = true,
    calibrating = false,
    connecting = false,
  }: {
    wakeWord?: string;
    awake?: boolean;
    requireWakeWord?: boolean;
    calibrating?: boolean;
    connecting?: boolean;
  } = {},
): string {
  switch (phase) {
    case 'offline':
      return connecting ? 'Connecting to JARVIS core…' : 'JARVIS core offline.';
    case 'thinking':
      return 'Thinking…';
    case 'speaking':
      return 'Speaking…';
    case 'hearing':
      return 'Listening…';
    case 'listening':
      if (calibrating) return 'Calibrating to the room. A second of quiet, please…';
      if (awake || !requireWakeWord) return "I'm listening.";
      return `Say “${capitalise(wakeWord)}” to wake me.`;
    default:
      return 'Standing by. Activate the microphone, or type below.';
  }
}

function Banner({ tone = 'info', children }: { tone?: 'info' | 'warn'; children: ReactNode }) {
  const color = tone === 'warn' ? '#f5a524' : '#22d3ee';
  return (
    <div
      className="w-full text-xs leading-relaxed rounded-lg px-3 py-2"
      style={{
        color: 'var(--color-text-secondary)',
        background: `color-mix(in srgb, ${color} 8%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 25%, transparent)`,
      }}
    >
      {children}
    </div>
  );
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="hud-mono px-1 py-0.5 rounded" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
      {children}
    </code>
  );
}

export function JarvisPage() {
  const serverInfo = useAppStore((s) => s.serverInfo);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const ttsAvailable = useTtsStore((s) => s.available);
  const ensureTtsHealth = useTtsStore((s) => s.ensureHealth);

  const [status, setStatus] = useState<JarvisStatus | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [speech, setSpeech] = useState<SpeechHealth | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState<'thinking' | 'speaking' | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [overheard, setOverheard] = useState('');
  const [muted, setMuted] = useState(false);
  const [draft, setDraft] = useState('');
  const [awake, setAwake] = useState(false);
  const [now, setNow] = useState(() => new Date());

  const coreRef = useRef<SVGGElement>(null);
  const logBoxRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const greeted = useRef(false);
  const processing = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const wakeWord = status?.wake_word || 'jarvis';
  const honorific = status?.honorific ?? 'sir';
  const followUpMs = Math.round((status?.follow_up_seconds ?? 8) * 1000);
  const requireWakeWord = status?.require_wake_word ?? true;
  const model = status?.model || serverInfo?.model || selectedModel || '';

  const modelRef = useRef(model);
  const mutedRef = useRef(muted);
  useEffect(() => {
    modelRef.current = model;
  }, [model]);
  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);

  const addLog = useCallback((kind: LogKind, text: string) => {
    const entry = { id: nextId.current++, kind, text, at: Date.now() };
    setLog((prev) => [...prev, entry].slice(-MAX_LOG));
  }, []);

  const conversation = useMemo(
    () =>
      new JarvisConversation({
        detector: new WakeWordDetector(wakeWord),
        honorific,
        followUpMs,
        ask: async (request, history) => {
          if (!modelRef.current) throw new Error('No model is configured on the server.');
          abortRef.current?.abort();
          const controller = new AbortController();
          abortRef.current = controller;
          return askJarvis(modelRef.current, history, request, controller.signal);
        },
        speak: async (text) => {
          if (!mutedRef.current) await speakAndWait(text);
        },
        emit: (event) => {
          if (event.kind === 'phase') setBusy(event.phase === 'done' ? null : event.phase);
          else if (event.kind === 'overheard') setOverheard(event.text);
          else {
            if (event.kind === 'you') setOverheard('');
            addLog(event.kind, event.text);
          }
        },
      }),
    [wakeWord, honorific, followUpMs, addLog],
  );

  const onLevel = useCallback((level: number) => {
    const core = coreRef.current;
    if (core) core.style.transform = `scale(${1 + Math.min(0.35, level * 6)})`;
  }, []);

  const utteranceRef = useRef<(utterance: Utterance) => void>(() => undefined);
  const mic = useJarvisMic({ onUtterance: (u) => utteranceRef.current(u), onLevel });
  const { setPaused, stop: stopMic } = mic;

  /** Run one request; `direct` skips the wake-word requirement. */
  const respond = useCallback(
    async (text: string, direct: boolean) => {
      const outcome = await conversation.handle(text, { direct });
      if (outcome === 'stop') stopMic();
    },
    [conversation, stopMic],
  );

  useEffect(() => {
    utteranceRef.current = (utterance: Utterance) => {
      if (processing.current) return;
      processing.current = true;
      void (async () => {
        try {
          setTranscribing(true);
          let text = '';
          try {
            const result = await transcribeAudio(utterance.wav, 'utterance.wav', {
              prompt: `${capitalise(wakeWord)}.`,
              vad: true,
            });
            text = (result.text ?? '').trim();
          } catch (error) {
            addLog('error', `Speech recognition failed: ${messageOf(error)}`);
            return;
          } finally {
            setTranscribing(false);
          }
          if (text) await respond(text, utterance.pushToTalk || !requireWakeWord);
        } finally {
          processing.current = false;
          setAwake(conversation.isAwake());
        }
      })();
    };
  }, [addLog, conversation, requireWakeWord, respond, wakeWord]);

  // Never listen to ourselves.
  useEffect(() => {
    setPaused(busy !== null || transcribing);
  }, [busy, transcribing, setPaused]);

  // Status and telemetry.
  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    const poll = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const next = await fetchJarvisStatus(controller.signal);
        if (cancelled) return;
        setStatus(next);
        setOnline(true);
      } catch (error) {
        if (cancelled || (error instanceof DOMException && error.name === 'AbortError')) return;
        setOnline(false);
      }
    };
    void poll();
    const timer = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
      controller?.abort();
    };
  }, []);

  // Which voice features the server offers (re-checked when it comes online).
  useEffect(() => {
    if (!online) return;
    fetchSpeechHealth()
      .then(setSpeech)
      .catch(() => setSpeech({ available: false }));
    void ensureTtsHealth();
  }, [online, ensureTtsHealth]);

  // Clock, and the follow-up window indicator.
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(new Date());
      setAwake(conversation.isAwake());
    }, 1000);
    return () => clearInterval(timer);
  }, [conversation]);

  // Keep the newest exchange in view. Scroll only the log box: scrollIntoView
  // would also scroll the page and hide the header on short screens.
  useEffect(() => {
    const box = logBoxRef.current;
    if (box && log.length > 0) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
  }, [log]);

  // Leaving the page: stop talking and drop any request in flight.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      stopSpeaking();
    },
    [],
  );

  const sttReady = speech?.available === true;
  const canTalk = online === true && sttReady;

  const activate = async () => {
    if (!(await mic.start())) return;
    conversation.sleep();
    if (greeted.current) return;
    greeted.current = true;
    const hello = greeting(honorific);
    addLog('jarvis', hello);
    if (mutedRef.current) return;
    setBusy('speaking');
    try {
      await speakAndWait(hello);
    } finally {
      setBusy(null);
    }
  };

  const deactivate = () => {
    mic.stop();
    conversation.sleep();
    setAwake(false);
  };

  const startPushToTalk = useCallback(() => {
    if (!canTalk || processing.current) return;
    stopSpeaking();
    void mic.beginPushToTalk();
  }, [canTalk, mic]);

  // Hold Space to talk (when not typing); Escape stops JARVIS mid-sentence.
  useEffect(() => {
    const typing = (target: EventTarget | null) =>
      target instanceof HTMLElement &&
      (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
    const down = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        stopSpeaking();
        return;
      }
      if (event.code !== 'Space' || event.repeat || typing(event.target)) return;
      event.preventDefault();
      startPushToTalk();
    };
    const up = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || typing(event.target)) return;
      event.preventDefault();
      mic.endPushToTalk();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, [mic, startPushToTalk]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || processing.current) return;
    setDraft('');
    processing.current = true;
    try {
      await respond(text, true);
    } finally {
      processing.current = false;
      setAwake(conversation.isAwake());
    }
  };

  const phase: ReactorPhase =
    online !== true
      ? 'offline'
      : busy ?? (transcribing || mic.pushToTalk ? 'hearing' : mic.active ? 'listening' : 'standby');
  const statusText = describePhase(phase, {
    wakeWord,
    awake,
    requireWakeWord,
    calibrating: mic.calibrating,
    connecting: online === null,
  });

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-4 md:px-8 pt-14 pb-6 md:py-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col items-center gap-5 min-w-0">
          <header className="w-full flex items-end justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-[0.35em]" style={{ color: '#22d3ee' }}>
                J.A.R.V.I.S.
              </h1>
              <p className="text-[11px] uppercase tracking-[0.2em]" style={{ color: 'var(--color-text-tertiary)' }}>
                Just A Rather Very Intelligent System
              </p>
            </div>
            <div className="text-right">
              <div className="text-lg md:text-xl hud-mono whitespace-nowrap" style={{ color: 'var(--color-text)' }}>
                {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </div>
              <div className="text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                {now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' })}
              </div>
            </div>
          </header>

          {online === false && (
            <Banner tone="warn">
              JARVIS core is offline. Start it with <Code>{SERVE_COMMAND}</Code>. This page reconnects by itself.
            </Banner>
          )}
          {online && status && !status.jarvis_mode && (
            <Banner>
              The server is running the “{status.agent ?? 'default'}” agent, so PC control and memory are off.
              Restart it with <Code>{SERVE_COMMAND}</Code> for the full JARVIS.
            </Banner>
          )}
          {online && speech && !speech.available && (
            <Banner>
              Speech recognition is not available on the server
              {speech.reason ? ` (${speech.reason})` : ''}. Install it with <Code>{INSTALL_COMMAND}</Code> and
              restart the server. Typing still works.
            </Banner>
          )}
          {online && ttsAvailable === false && (
            <Banner>
              Voice output is off. For the British voice run <Code>{INSTALL_COMMAND}</Code> and restart the server.
            </Banner>
          )}

          <ArcReactor phase={phase} size={300} coreRef={coreRef} />

          <div className="flex flex-col items-center gap-1 text-center min-h-[3rem]" aria-live="polite">
            <p className="text-sm tracking-wide" style={{ color: 'var(--color-text)' }}>
              {statusText}
            </p>
            {overheard && phase === 'listening' && (
              <p className="text-xs italic" style={{ color: 'var(--color-text-tertiary)' }}>
                Heard “{overheard}”. Start with “{capitalise(wakeWord)}” and I’ll respond.
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2">
            {mic.active ? (
              <button type="button" onClick={deactivate} className="jarvis-button" aria-pressed="true">
                <MicOff size={15} /> Deactivate
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void activate()}
                disabled={!canTalk}
                className="jarvis-button jarvis-button--primary"
                title={canTalk ? 'Listen for the wake word' : 'Needs the server and speech recognition'}
              >
                <Mic size={15} /> Activate
              </button>
            )}
            <button
              type="button"
              disabled={!canTalk}
              className="jarvis-button"
              aria-pressed={mic.pushToTalk}
              onPointerDown={startPushToTalk}
              onPointerUp={() => mic.endPushToTalk()}
              onPointerLeave={() => mic.endPushToTalk()}
              onPointerCancel={() => mic.endPushToTalk()}
              title="Hold to talk (or hold Space)"
            >
              <Hand size={15} /> Hold to talk
            </button>
            <button
              type="button"
              className="jarvis-button"
              onClick={() => {
                if (!muted) stopSpeaking();
                setMuted(!muted);
              }}
              aria-pressed={muted}
              title={muted ? 'Unmute JARVIS' : 'Mute JARVIS'}
            >
              {muted ? <VolumeX size={15} /> : <Volume2 size={15} />} {muted ? 'Muted' : 'Voice on'}
            </button>
            {busy === 'speaking' && (
              <button type="button" className="jarvis-button" onClick={stopSpeaking} title="Stop talking (Esc)">
                <Square size={13} /> Stop
              </button>
            )}
          </div>
          {mic.error && (
            <p className="text-xs" style={{ color: '#f87171' }}>
              {mic.error}
            </p>
          )}

          <section className="hud-panel w-full p-4 flex flex-col gap-3" aria-label="Conversation">
            <div ref={logBoxRef} className="max-h-[34vh] min-h-[6rem] overflow-y-auto flex flex-col gap-2.5 pr-1">
              {log.length === 0 && (
                <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                  Try “{capitalise(wakeWord)}, what’s the time?”, “open Notepad”, “play lo-fi beats on YouTube”,
                  “how’s my battery?” or “remember that my name is Tony”.
                </p>
              )}
              {log.map((entry) => (
                <div key={entry.id} className="flex gap-3 text-sm leading-relaxed">
                  <span
                    className="hud-mono text-[10px] pt-1 shrink-0 w-12 uppercase tracking-wider"
                    style={{
                      color: entry.kind === 'jarvis' ? '#22d3ee' : entry.kind === 'error' ? '#f87171' : 'var(--color-text-tertiary)',
                    }}
                  >
                    {entry.kind === 'jarvis' ? 'Jarvis' : entry.kind === 'error' ? 'Error' : 'You'}
                  </span>
                  <p className="whitespace-pre-wrap break-words min-w-0" style={{ color: 'var(--color-text)' }}>
                    {entry.text}
                  </p>
                </div>
              ))}
            </div>
            <form onSubmit={(event) => void submit(event)} className="flex gap-2">
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={online ? 'Type a request for JARVIS…' : 'Waiting for JARVIS core…'}
                aria-label="Request"
                disabled={online !== true}
                className="flex-1 min-w-0 rounded-lg px-3 py-2 text-sm outline-none"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text)',
                }}
              />
              <button
                type="submit"
                className="jarvis-button jarvis-button--primary"
                disabled={online !== true || !draft.trim() || busy !== null}
                aria-label="Send"
              >
                <SendHorizontal size={15} />
              </button>
            </form>
          </section>
        </div>

        <aside className="min-w-0">
          <StatsPanel status={status} />
        </aside>
      </div>
    </div>
  );
}
