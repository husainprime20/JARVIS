/** Server calls used by the JARVIS HUD. */
import { apiFetch } from '../api';
import { useTtsStore } from '../tts';
import type { ChatTurn } from './conversation';

export interface SystemStats {
  os?: string;
  cpu?: { percent: number; logical_cores: number };
  memory?: { used_gb: number; total_gb: number; percent: number };
  disk?: { path: string; used_gb: number; total_gb: number; percent: number };
  battery?: { percent: number; plugged_in: boolean; time_left?: string };
  uptime?: string;
  summary?: string;
  error?: string;
}

export interface JarvisStatus {
  agent: string | null;
  jarvis_mode: boolean;
  model: string;
  engine: string;
  tools: string[];
  wake_word: string;
  honorific: string;
  follow_up_seconds: number;
  require_wake_word: boolean;
  voice: string;
  stats: SystemStats;
}

export async function fetchJarvisStatus(signal?: AbortSignal): Promise<JarvisStatus> {
  const res = await apiFetch('/v1/jarvis/status', { signal });
  if (!res.ok) throw new Error(`JARVIS status unavailable (${res.status})`);
  return res.json();
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail ?? body?.error?.message ?? body?.error;
    if (typeof detail === 'string' && detail) return detail;
  } catch {
    // Not JSON; fall back to the status line below.
  }
  return res.statusText || 'request failed';
}

/** One non-streaming turn through the server's agent (JARVIS when configured). */
export async function askJarvis(
  model: string,
  history: ChatTurn[],
  request: string,
  signal?: AbortSignal,
): Promise<string> {
  const res = await apiFetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: [...history, { role: 'user', content: request }],
      stream: false,
    }),
    signal,
  });
  if (!res.ok) throw new Error(`${res.status} ${await errorDetail(res)}`);
  const data = await res.json();
  const content = data?.choices?.[0]?.message?.content;
  return typeof content === 'string' ? content : '';
}

const HUD_PREFIX = 'jarvis-hud-';
let utterance = 0;

/**
 * Speak through the app's shared voice and resolve when playback ends (or
 * fails), so the HUD knows when it may listen again.
 */
export async function speakAndWait(text: string): Promise<void> {
  const store = useTtsStore;
  // The server said it has no voice: show the reply without a failing request.
  if (!text.trim() || store.getState().available === false) return;
  utterance += 1;
  const id = `${HUD_PREFIX}${utterance}`;
  await store.getState().speak(id, text);
  if (store.getState().speakingId !== id) return; // failed, or already finished
  await new Promise<void>((resolve) => {
    const unsubscribe = store.subscribe((state) => {
      if (state.speakingId !== id) {
        unsubscribe();
        resolve();
      }
    });
  });
}

/** Stop the HUD's own speech (never a chat message being read aloud). */
export function stopSpeaking(): void {
  const { speakingId, stop } = useTtsStore.getState();
  if (speakingId?.startsWith(HUD_PREFIX)) stop();
}
