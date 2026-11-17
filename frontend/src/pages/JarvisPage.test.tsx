import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../lib/store', () => ({
  useAppStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ serverInfo: null, selectedModel: '' }),
}));
vi.mock('../lib/tts', () => ({
  useTtsStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ available: null, ensureHealth: vi.fn(async () => undefined) }),
}));
vi.mock('../lib/api', () => ({
  fetchSpeechHealth: vi.fn(),
  transcribeAudio: vi.fn(),
}));
vi.mock('../lib/jarvis/api', () => ({
  askJarvis: vi.fn(),
  fetchJarvisStatus: vi.fn(),
  speakAndWait: vi.fn(),
  stopSpeaking: vi.fn(),
}));
vi.mock('../hooks/useJarvisMic', () => ({
  useJarvisMic: () => ({
    active: false,
    calibrating: false,
    pushToTalk: false,
    error: null,
    start: vi.fn(),
    stop: vi.fn(),
    setPaused: vi.fn(),
    beginPushToTalk: vi.fn(),
    endPushToTalk: vi.fn(),
  }),
}));

import { StatsPanel, gaugeColor } from '../components/Jarvis/StatsPanel';
import type { JarvisStatus } from '../lib/jarvis/api';
import { JarvisPage, describePhase } from './JarvisPage';

describe('JarvisPage', () => {
  it('renders the Rust-first JARVIS assistant interface', () => {
    const html = renderToStaticMarkup(<JarvisPage />);
    expect(html).toContain('JARVIS');
    expect(html).toContain('PERSONAL AI SYSTEM');
    expect(html).toContain('Ask JARVIS anything');
    expect(html).toContain('CTRL + ALT + J TO SUMMON');
    expect(html).toContain('At your service');
  });
});

describe('describePhase', () => {
  it('tells the user what JARVIS is doing', () => {
    expect(describePhase('offline', { connecting: true })).toBe('Connecting to JARVIS core…');
    expect(describePhase('offline')).toBe('JARVIS core offline.');
    expect(describePhase('listening')).toBe('Say “Jarvis” to wake me.');
    expect(describePhase('listening', { wakeWord: 'friday' })).toBe('Say “Friday” to wake me.');
    expect(describePhase('listening', { awake: true })).toBe("I'm listening.");
    expect(describePhase('listening', { requireWakeWord: false })).toBe("I'm listening.");
    expect(describePhase('listening', { calibrating: true })).toMatch(/Calibrating/);
    expect(describePhase('thinking')).toBe('Thinking…');
    expect(describePhase('speaking')).toBe('Speaking…');
    expect(describePhase('standby')).toMatch(/Standing by/);
  });
});

describe('StatsPanel', () => {
  const status: JarvisStatus = {
    agent: 'jarvis',
    jarvis_mode: true,
    model: 'gemini-3.5-flash-lite',
    engine: 'multi',
    tools: ['open_app', 'system_stats'],
    wake_word: 'jarvis',
    honorific: 'sir',
    follow_up_seconds: 8,
    require_wake_word: true,
    voice: 'kokoro / bm_george',
    stats: {
      os: 'Windows 11',
      cpu: { percent: 12, logical_cores: 8 },
      memory: { used_gb: 6.1, total_gb: 16, percent: 38 },
      disk: { path: 'C:\\', used_gb: 120, total_gb: 476, percent: 25 },
      battery: { percent: 15, plugged_in: false, time_left: '40 minutes' },
      uptime: '3 hours',
    },
  };

  it('shows telemetry, the brain and the tools', () => {
    const html = renderToStaticMarkup(<StatsPanel status={status} />);
    expect(html).toContain('Windows 11');
    expect(html).toContain('6.1 of 16 GB');
    expect(html).toContain('Drive C:');
    expect(html).toContain('40 minutes left');
    expect(html).toContain('gemini-3.5-flash-lite');
    expect(html).toContain('kokoro / bm_george');
    expect(html).toContain('open_app');
    // A low battery is a warning even though its percentage is small.
    expect(html).toContain(`background-color:${gaugeColor(85)}`);
  });

  it('waits politely for telemetry', () => {
    expect(renderToStaticMarkup(<StatsPanel status={null} />)).toContain('Waiting for telemetry');
  });

  it('colours gauges by load', () => {
    expect(gaugeColor(10)).toBe('#22d3ee');
    expect(gaugeColor(75)).toBe('#f5a524');
    expect(gaugeColor(95)).toBe('#f87171');
  });
});
