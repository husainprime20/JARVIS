/**
 * Microphone helpers for the JARVIS HUD: utterance detection and WAV encoding.
 *
 * Mirrors `record_utterance` in `openjarvis/speech/voice_io.py`: wait for
 * speech, keep a short pre-roll so the first syllable is not clipped, stop
 * after a pause, and drop clicks that are too short to be words. Pure
 * functions over Float32 frames, so they are unit-testable without a mic.
 */

export function rms(frame: Float32Array): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

export interface UtteranceOptions {
  sampleRate: number;
  /** RMS level (0..1) above which audio counts as speech. */
  threshold?: number;
  /** Pause that ends an utterance. */
  silenceMs?: number;
  /** Audio kept from before speech started. */
  preRollMs?: number;
  /** Shorter bursts (clicks, bumps) are ignored. */
  minSpeechMs?: number;
  /** Hard cap on one utterance. */
  maxMs?: number;
}

export const DEFAULT_THRESHOLD = 0.015;

export class UtteranceDetector {
  readonly sampleRate: number;
  threshold: number;
  /** Level of the most recent frame, for the HUD's meter. */
  level = 0;
  private readonly silenceSamples: number;
  private readonly preRollSamples: number;
  private readonly minSpeechSamples: number;
  private readonly maxSamples: number;
  private preRoll: Float32Array[] = [];
  private preRollLength = 0;
  private frames: Float32Array[] = [];
  private captured = 0;
  private loud = 0;
  private silent = 0;

  constructor(options: UtteranceOptions) {
    const ms = (value: number) => Math.round((value / 1000) * options.sampleRate);
    this.sampleRate = options.sampleRate;
    this.threshold = options.threshold ?? DEFAULT_THRESHOLD;
    this.silenceSamples = ms(options.silenceMs ?? 900);
    this.preRollSamples = ms(options.preRollMs ?? 400);
    this.minSpeechSamples = ms(options.minSpeechMs ?? 250);
    this.maxSamples = ms(options.maxMs ?? 15000);
  }

  /** True while an utterance is being captured. */
  get capturing(): boolean {
    return this.frames.length > 0;
  }

  /** Feed one frame; returns a finished utterance, or `null`. */
  push(input: Float32Array): Float32Array | null {
    const frame = new Float32Array(input); // the audio graph reuses its buffers
    this.level = rms(frame);
    const isLoud = this.level > this.threshold;

    if (!this.capturing) {
      if (isLoud) {
        this.frames = [...this.preRoll, frame];
        this.captured = this.preRollLength + frame.length;
        this.loud = frame.length;
        this.silent = 0;
        this.preRoll = [];
        this.preRollLength = 0;
      } else {
        this.preRoll.push(frame);
        this.preRollLength += frame.length;
        while (this.preRoll.length > 1 && this.preRollLength - this.preRoll[0].length >= this.preRollSamples) {
          this.preRollLength -= this.preRoll.shift()!.length;
        }
      }
      return null;
    }

    this.frames.push(frame);
    this.captured += frame.length;
    if (isLoud) {
      this.loud += frame.length;
      this.silent = 0;
    } else {
      this.silent += frame.length;
    }
    if (this.silent >= this.silenceSamples || this.captured >= this.maxSamples) {
      return this.finish();
    }
    return null;
  }

  /** Forget anything captured so far (e.g. while JARVIS is speaking). */
  reset(): void {
    this.frames = [];
    this.captured = 0;
    this.loud = 0;
    this.silent = 0;
    this.preRoll = [];
    this.preRollLength = 0;
  }

  /** End the current utterance now (push-to-talk release). */
  flush(): Float32Array | null {
    return this.capturing ? this.finish() : null;
  }

  private finish(): Float32Array | null {
    const enoughSpeech = this.loud >= this.minSpeechSamples;
    const out = enoughSpeech ? concat(this.frames, this.captured) : null;
    this.reset();
    return out;
  }
}

function concat(frames: Float32Array[], length: number): Float32Array {
  const out = new Float32Array(length);
  let offset = 0;
  for (const frame of frames) {
    out.set(frame, offset);
    offset += frame.length;
  }
  return out;
}

/** Learns the room's background level and suggests a speech threshold. */
export class NoiseCalibrator {
  private levels: number[] = [];
  private samples = 0;
  private readonly needed: number;

  constructor(sampleRate: number, durationMs = 1000) {
    this.needed = Math.round((durationMs / 1000) * sampleRate);
  }

  get done(): boolean {
    return this.samples >= this.needed;
  }

  push(frame: Float32Array): void {
    this.levels.push(rms(frame));
    this.samples += frame.length;
  }

  /** Three times the median level, kept within sensible bounds. */
  threshold(): number {
    if (this.levels.length === 0) return DEFAULT_THRESHOLD;
    const sorted = [...this.levels].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    return Math.min(0.08, Math.max(0.012, median * 3));
  }
}

/** Average-downsample `input` to `toRate` (speech recognition wants 16 kHz). */
export function downsample(input: Float32Array, fromRate: number, toRate = 16000): Float32Array {
  if (toRate >= fromRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

/** Encode mono samples as a 16-bit PCM WAV file. */
export function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}
