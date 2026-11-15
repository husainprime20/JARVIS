import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('../api', () => ({ apiFetch: mocks.apiFetch }));
vi.mock('../tts', async () => {
  const { create } = await import('zustand');
  interface FakeTts {
    available: boolean | null;
    speakingId: string | null;
    speak: (id: string, text: string) => Promise<void>;
    stop: () => void;
  }
  const useTtsStore = create<FakeTts>((set) => ({
    available: true,
    speakingId: null,
    speak: vi.fn(async (id: string) => set({ speakingId: id })),
    stop: vi.fn(() => set({ speakingId: null })),
  }));
  return { useTtsStore };
});

import { useTtsStore } from '../tts';
import { askJarvis, fetchJarvisStatus, speakAndWait, stopSpeaking } from './api';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  vi.clearAllMocks();
  useTtsStore.setState({ available: true, speakingId: null });
});

describe('speakAndWait', () => {
  it('resolves only when playback has finished', async () => {
    let done = false;
    const speaking = speakAndWait('Good evening, sir.').then(() => {
      done = true;
    });
    await flush();
    const id = useTtsStore.getState().speakingId;
    expect(id).toMatch(/^jarvis-hud-/);
    expect(done).toBe(false);
    useTtsStore.setState({ speakingId: null }); // the audio ended
    await speaking;
    expect(done).toBe(true);
  });

  it('does not ask for speech when the server has no voice', async () => {
    useTtsStore.setState({ available: false });
    await speakAndWait('Hello');
    expect(useTtsStore.getState().speak).not.toHaveBeenCalled();
  });

  it('ignores empty text', async () => {
    await speakAndWait('   ');
    expect(useTtsStore.getState().speak).not.toHaveBeenCalled();
  });
});

describe('stopSpeaking', () => {
  it("stops the HUD's speech but never a chat message being read aloud", () => {
    useTtsStore.setState({ speakingId: 'message-42' });
    stopSpeaking();
    expect(useTtsStore.getState().stop).not.toHaveBeenCalled();

    useTtsStore.setState({ speakingId: 'jarvis-hud-7' });
    stopSpeaking();
    expect(useTtsStore.getState().stop).toHaveBeenCalledTimes(1);
  });
});

describe('askJarvis', () => {
  it('sends the history plus the request as one non-streaming turn', async () => {
    mocks.apiFetch.mockResolvedValue(json({ choices: [{ message: { content: 'It is 9 PM, sir.' } }] }));
    const history = [
      { role: 'user' as const, content: 'hi' },
      { role: 'assistant' as const, content: 'Hello, sir.' },
    ];
    await expect(askJarvis('gemini-3.5-flash-lite', history, 'what time is it')).resolves.toBe(
      'It is 9 PM, sir.',
    );
    const [path, init] = mocks.apiFetch.mock.calls[0];
    expect(path).toBe('/v1/chat/completions');
    expect(JSON.parse(init.body)).toEqual({
      model: 'gemini-3.5-flash-lite',
      messages: [...history, { role: 'user', content: 'what time is it' }],
      stream: false,
    });
  });

  it('includes the status and server detail in errors', async () => {
    mocks.apiFetch.mockResolvedValue(json({ detail: 'RESOURCE_EXHAUSTED: quota' }, 429));
    await expect(askJarvis('m', [], 'hello')).rejects.toThrow('429 RESOURCE_EXHAUSTED: quota');
  });

  it('copes with a reply that has no text', async () => {
    mocks.apiFetch.mockResolvedValue(json({ choices: [] }));
    await expect(askJarvis('m', [], 'hello')).resolves.toBe('');
  });
});

describe('fetchJarvisStatus', () => {
  it('returns the status, and throws when the server cannot provide it', async () => {
    mocks.apiFetch.mockResolvedValueOnce(json({ agent: 'jarvis' }));
    await expect(fetchJarvisStatus()).resolves.toEqual({ agent: 'jarvis' });
    mocks.apiFetch.mockResolvedValueOnce(json({ detail: 'Not Found' }, 404));
    await expect(fetchJarvisStatus()).rejects.toThrow('JARVIS status unavailable (404)');
  });
});
