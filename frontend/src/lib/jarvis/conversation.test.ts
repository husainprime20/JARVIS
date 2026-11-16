import { describe, expect, it, vi } from 'vitest';
import {
  JarvisConversation,
  friendlyError,
  greeting,
  spokenVersion,
  type ChatTurn,
  type JarvisEvent,
} from './conversation';

function setup(overrides: Partial<ConstructorParameters<typeof JarvisConversation>[0]> = {}) {
  let clock = 1_000;
  const events: JarvisEvent[] = [];
  const spoken: string[] = [];
  const ask = vi.fn(async (request: string, _history: ChatTurn[]) => `Answer to ${request}`);
  const conversation = new JarvisConversation({
    ask,
    speak: async (text) => {
      spoken.push(text);
    },
    emit: (event) => events.push(event),
    now: () => clock,
    followUpMs: 8000,
    ...overrides,
  });
  return {
    conversation,
    ask,
    events,
    spoken,
    advance: (ms: number) => {
      clock += ms;
    },
  };
}

describe('JarvisConversation', () => {
  it('ignores speech without the wake word, but reports what it overheard', async () => {
    const { conversation, ask, events } = setup();
    expect(await conversation.handle('open spotify')).toBe('ignored');
    expect(ask).not.toHaveBeenCalled();
    expect(events).toEqual([{ kind: 'overheard', text: 'open spotify' }]);
  });

  it('answers a wake-word request and remembers the exchange', async () => {
    const { conversation, ask, events, spoken } = setup();
    expect(await conversation.handle('Jarvis, what time is it?')).toBe('answered');
    expect(ask).toHaveBeenCalledWith('what time is it', []);
    expect(spoken).toEqual(['Answer to what time is it']);
    expect(events.map((e) => e.kind)).toEqual(['you', 'phase', 'jarvis', 'phase', 'phase']);
    expect(conversation.history).toEqual([
      { role: 'user', content: 'what time is it' },
      { role: 'assistant', content: 'Answer to what time is it' },
    ]);
  });

  it('accepts follow-ups without the name until the window closes', async () => {
    const { conversation, ask, advance } = setup();
    await conversation.handle('Jarvis, what is the weather?');
    advance(5000);
    expect(conversation.isAwake()).toBe(true);
    expect(await conversation.handle('and tomorrow?')).toBe('answered');
    expect(ask).toHaveBeenLastCalledWith('and tomorrow?', expect.any(Array));
    advance(9000);
    expect(conversation.isAwake()).toBe(false);
    expect(await conversation.handle('and the day after?')).toBe('ignored');
  });

  it('acknowledges the name alone and starts listening', async () => {
    const { conversation, spoken, ask } = setup();
    expect(await conversation.handle('Jarvis?')).toBe('ack');
    expect(spoken).toEqual(['Yes, sir?']);
    expect(conversation.isAwake()).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });

  it('signs off on goodbye and goes back to sleep', async () => {
    const { conversation, spoken } = setup();
    await conversation.handle('Jarvis?');
    expect(await conversation.handle('Goodbye, Jarvis.')).toBe('stop');
    expect(spoken[spoken.length - 1]).toBe('Very good, sir. Signing off.');
    expect(conversation.isAwake()).toBe(false);
  });

  it('cancels with "never mind"', async () => {
    const { conversation, ask } = setup();
    await conversation.handle('Jarvis?');
    expect(await conversation.handle('never mind')).toBe('cancel');
    expect(ask).not.toHaveBeenCalled();
    expect(conversation.isAwake()).toBe(false);
  });

  it('treats typed or push-to-talk input as a request, even without the name', async () => {
    const { conversation, ask } = setup();
    expect(await conversation.handle('thank you', { direct: true })).toBe('answered');
    expect(ask).toHaveBeenCalledWith('thank you', []);
  });

  it('drops Whisper noise in hands-free mode', async () => {
    const { conversation, events } = setup();
    expect(await conversation.handle('Thank you.')).toBe('ignored');
    expect(events).toEqual([]);
  });

  it('speaks a kind explanation when the brain fails', async () => {
    const { conversation, events, spoken } = setup({
      ask: async () => {
        throw new Error('429 RESOURCE_EXHAUSTED');
      },
    });
    expect(await conversation.handle('Jarvis, open Spotify')).toBe('error');
    expect(events).toContainEqual({ kind: 'error', text: '429 RESOURCE_EXHAUSTED' });
    expect(spoken[0]).toMatch(/usage limit/);
    expect(conversation.history).toEqual([]);
  });

  it('keeps only the most recent history', async () => {
    const { conversation } = setup({ maxHistory: 4 });
    for (const n of [1, 2, 3]) await conversation.handle(`Jarvis, question ${n}`);
    expect(conversation.history.map((t) => t.content)).toEqual([
      'question 2',
      'Answer to question 2',
      'question 3',
      'Answer to question 3',
    ]);
  });

  it('uses the configured honorific, or none', async () => {
    const boss = setup({ honorific: 'boss' });
    await boss.conversation.handle('Jarvis');
    expect(boss.spoken).toEqual(['Yes, boss?']);
    const plain = setup({ honorific: '' });
    await plain.conversation.handle('Jarvis');
    expect(plain.spoken).toEqual(['Yes?']);
  });
});

describe('helpers', () => {
  it('turns markdown into speech and trims long replies at a sentence', () => {
    expect(spokenVersion('**Done.** See [the docs](https://example.com).')).toBe('Done. See the docs.');
    const long = `${'This is a sentence. '.repeat(60)}`;
    const spoken = spokenVersion(long, 200);
    expect(spoken.length).toBeLessThan(260);
    expect(spoken.endsWith('The rest is on screen.')).toBe(true);
  });

  it('explains common failures in plain words', () => {
    expect(friendlyError(new Error('401 Unauthorized'))).toMatch(/API key was rejected, sir/);
    expect(friendlyError(new TypeError('Failed to fetch'), '')).toMatch(/can't reach my brain right now\./);
    expect(friendlyError('boom', 'boss')).toMatch(/something went wrong, boss/);
  });

  it('explains what the server reports when the brain fails', () => {
    // askJarvis throws "<status> <server detail>".
    expect(
      friendlyError(
        new Error(
          '500 Ollama returned 500: model requires more system memory (3.1 GiB) than is available (2.4 GiB)',
        ),
      ),
    ).toMatch(/needs more free memory than this PC has right now, sir\. Close a few programs/);
    expect(friendlyError(new Error('503 Ollama not reachable at http://localhost:11434'))).toMatch(
      /Ollama, isn't responding, sir/,
    );
    // The error from Windows installers that left out Ollama's model runtime.
    expect(
      friendlyError(
        new Error(
          "500 Ollama returned 500: error starting llama-server: llama-server binary not found (checked: C:\\Users\\NOVA\\AppData\\Local\\OpenJarvis\\llama-server.exe). Run 'cmake -S llama/server --preset cpu' first",
        ),
      ),
    ).toMatch(/couldn't start its model, sir\. Installing the latest version/);
    expect(friendlyError(new Error('500 Ollama returned 500: llama runner process has terminated: exit status 2'))).toMatch(
      /couldn't start its model/,
    );
    expect(friendlyError(new Error('500 429 RESOURCE_EXHAUSTED: quota exceeded'))).toMatch(/usage limit/);
    expect(friendlyError(new Error('500 403 PERMISSION_DENIED'))).toMatch(/API key was rejected/);
    // What the server reports for a mistyped Gemini key (the cloud source).
    expect(
      friendlyError(
        new Error(
          "500 400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', 'status': 'INVALID_ARGUMENT'}}",
        ),
      ),
    ).toMatch(/API key was rejected, sir/);
  });

  it('points offline-brain failures at the cloud source in Settings', () => {
    const hint = /switch me to a free Gemini key in Settings, under Inference source\./;
    expect(friendlyError(new Error('500 Ollama returned 500: model requires more system memory (3.1 GiB) than is available (2.4 GiB)'))).toMatch(hint);
    expect(friendlyError(new Error('500 Ollama returned 500: llama-server binary not found'))).toMatch(hint);
  });

  it('does not mistake numbers or words in error details for status codes', () => {
    expect(friendlyError(new Error('500 prompt of 1429 tokens is too long'))).toMatch(/something went wrong/);
    expect(friendlyError(new Error("500 [Errno 13] Permission denied: 'memory.db'"))).toMatch(
      /something went wrong/,
    );
    expect(friendlyError(new Error('500 needs 4.03 GB'))).toMatch(/something went wrong/);
  });

  it('greets by the time of day', () => {
    expect(greeting('sir', new Date(2026, 10, 15, 8))).toBe('Good morning, sir. JARVIS online and at your service.');
    expect(greeting('sir', new Date(2026, 10, 15, 14))).toMatch(/^Good afternoon/);
    expect(greeting('', new Date(2026, 10, 15, 22))).toBe('Good evening. JARVIS online and at your service.');
  });
});
