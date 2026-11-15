import { describe, expect, it } from 'vitest';
import { NoiseCalibrator, UtteranceDetector, downsample, encodeWav, rms } from './audio';

const RATE = 16000;
const FRAME = 1600; // 100 ms

function tone(level: number, length = FRAME): Float32Array {
  // A square wave has RMS equal to its amplitude.
  return Float32Array.from({ length }, (_, i) => (i % 2 === 0 ? level : -level));
}

function feed(detector: UtteranceDetector, frames: Float32Array[]): Float32Array[] {
  const out: Float32Array[] = [];
  for (const frame of frames) {
    const utterance = detector.push(frame);
    if (utterance) out.push(utterance);
  }
  return out;
}

const repeat = (frame: Float32Array, n: number) => Array.from({ length: n }, () => frame);

describe('rms', () => {
  it('measures loudness', () => {
    expect(rms(new Float32Array(0))).toBe(0);
    expect(rms(tone(0.5))).toBeCloseTo(0.5);
  });
});

describe('UtteranceDetector', () => {
  const options = { sampleRate: RATE, threshold: 0.05, silenceMs: 300, preRollMs: 200, minSpeechMs: 200 };

  it('emits one utterance after speech followed by a pause, with pre-roll', () => {
    const detector = new UtteranceDetector(options);
    const quiet = tone(0.001);
    const loud = tone(0.3);
    const out = feed(detector, [...repeat(quiet, 5), ...repeat(loud, 5), ...repeat(quiet, 3)]);
    expect(out).toHaveLength(1);
    // 2 pre-roll frames (200 ms) + 5 speech + 3 trailing silence.
    expect(out[0].length).toBe(10 * FRAME);
    expect(detector.capturing).toBe(false);
  });

  it('ignores clicks shorter than the minimum speech length', () => {
    const detector = new UtteranceDetector(options);
    const out = feed(detector, [tone(0.3), ...repeat(tone(0.001), 4)]);
    expect(out).toHaveLength(0);
  });

  it('caps very long speech', () => {
    const detector = new UtteranceDetector({ ...options, maxMs: 1000 });
    const out = feed(detector, repeat(tone(0.3), 12));
    expect(out).toHaveLength(1);
    expect(out[0].length).toBe(10 * FRAME);
  });

  it('copies frames, because the audio graph reuses its buffers', () => {
    const detector = new UtteranceDetector(options);
    const shared = tone(0.3);
    for (let i = 0; i < 3; i += 1) detector.push(shared);
    shared.fill(0);
    const utterance = detector.flush();
    expect(utterance).not.toBeNull();
    expect(rms(utterance!)).toBeGreaterThan(0.2);
  });

  it('reset drops a half-captured utterance', () => {
    const detector = new UtteranceDetector(options);
    feed(detector, repeat(tone(0.3), 3));
    detector.reset();
    expect(detector.capturing).toBe(false);
    expect(detector.flush()).toBeNull();
  });
});

describe('NoiseCalibrator', () => {
  it('sets the threshold above the room noise, within bounds', () => {
    const calibrator = new NoiseCalibrator(RATE, 500);
    expect(calibrator.done).toBe(false);
    for (let i = 0; i < 5; i += 1) calibrator.push(tone(0.01));
    expect(calibrator.done).toBe(true);
    expect(calibrator.threshold()).toBeCloseTo(0.03);

    const silent = new NoiseCalibrator(RATE, 100);
    silent.push(tone(0));
    expect(silent.threshold()).toBe(0.012);

    const noisy = new NoiseCalibrator(RATE, 100);
    noisy.push(tone(0.5));
    expect(noisy.threshold()).toBe(0.08);
  });
});

describe('downsample', () => {
  it('averages down to 16 kHz and leaves lower rates alone', () => {
    const input = Float32Array.from({ length: 48 }, (_, i) => i);
    const out = downsample(input, 48000, 16000);
    expect(out.length).toBe(16);
    expect(out[0]).toBeCloseTo(1); // mean of 0, 1, 2
    const low = new Float32Array(10);
    expect(downsample(low, 8000, 16000)).toBe(low);
  });
});

describe('encodeWav', () => {
  it('writes a 16-bit mono PCM WAV header and clipped samples', () => {
    const wav = encodeWav(Float32Array.from([0, 1, -1, 2]), RATE);
    const view = new DataView(wav);
    const ascii = (offset: number) =>
      String.fromCharCode(...new Uint8Array(wav, offset, 4));
    expect(wav.byteLength).toBe(44 + 8);
    expect(ascii(0)).toBe('RIFF');
    expect(ascii(8)).toBe('WAVE');
    expect(ascii(36)).toBe('data');
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(RATE);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getInt16(46, true)).toBe(0x7fff);
    expect(view.getInt16(48, true)).toBe(-0x8000);
    expect(view.getInt16(50, true)).toBe(0x7fff); // 2.0 is clipped
  });
});
