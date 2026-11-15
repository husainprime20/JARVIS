import { useCallback, useEffect, useRef, useState } from 'react';
import {
  NoiseCalibrator,
  UtteranceDetector,
  downsample,
  encodeWav,
  rms,
} from '../lib/jarvis/audio';

export interface Utterance {
  wav: Blob;
  /** Recorded while push-to-talk was held: always treated as a request. */
  pushToTalk: boolean;
}

interface MicHandles {
  context: AudioContext;
  stream: MediaStream;
  nodes: AudioNode[];
}

const PTT_MAX_MS = 30000;

/**
 * Always-on microphone for the HUD: emits one WAV blob per spoken utterance.
 *
 * Uses a ScriptProcessorNode rather than an AudioWorklet: it works in every
 * browser and in the desktop app's webview without loading a worker script,
 * which the desktop Content-Security-Policy does not allow.
 */
export function useJarvisMic(options: {
  onUtterance: (utterance: Utterance) => void;
  onLevel?: (level: number) => void;
}) {
  const [active, setActive] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  const [pushToTalk, setPushToTalk] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handles = useRef<MicHandles | null>(null);
  const paused = useRef(false);
  const ptt = useRef<{ frames: Float32Array[]; samples: number } | null>(null);
  const callbacks = useRef(options);
  callbacks.current = options;

  const emit = useCallback((samples: Float32Array, sampleRate: number, isPtt: boolean) => {
    const pcm = downsample(samples, sampleRate, 16000);
    const wav = new Blob([encodeWav(pcm, 16000)], { type: 'audio/wav' });
    callbacks.current.onUtterance({ wav, pushToTalk: isPtt });
  }, []);

  const stop = useCallback(() => {
    const h = handles.current;
    handles.current = null;
    ptt.current = null;
    setPushToTalk(false);
    if (h) {
      for (const node of h.nodes) {
        try {
          node.disconnect();
        } catch {
          // Already disconnected.
        }
      }
      h.stream.getTracks().forEach((track) => track.stop());
      void h.context.close().catch(() => undefined);
    }
    setActive(false);
    setCalibrating(false);
    callbacks.current.onLevel?.(0);
  }, []);

  const start = useCallback(async (): Promise<boolean> => {
    if (handles.current) return true;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser cannot use the microphone.');
      return false;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setError('Microphone access was blocked. Allow it in the browser and try again.');
      return false;
    }

    const AudioCtx: typeof AudioContext =
      window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const context = new AudioCtx();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const mute = context.createGain();
    mute.gain.value = 0; // keep the graph running without echoing the mic
    source.connect(processor);
    processor.connect(mute);
    mute.connect(context.destination);

    const sampleRate = context.sampleRate;
    const calibrator = new NoiseCalibrator(sampleRate, 1000);
    const detector = new UtteranceDetector({ sampleRate });
    setCalibrating(true);

    processor.onaudioprocess = (event: AudioProcessingEvent) => {
      const frame = event.inputBuffer.getChannelData(0);
      const held = ptt.current;
      if (held) {
        held.frames.push(new Float32Array(frame));
        held.samples += frame.length;
        callbacks.current.onLevel?.(rms(frame));
        if (held.samples >= (PTT_MAX_MS / 1000) * sampleRate) endPushToTalkRef.current();
        return;
      }
      if (paused.current) {
        // JARVIS is thinking or talking: neither calibrate on nor record his voice.
        detector.reset();
        callbacks.current.onLevel?.(0);
        return;
      }
      if (!calibrator.done) {
        calibrator.push(frame);
        callbacks.current.onLevel?.(rms(frame));
        if (calibrator.done) {
          detector.threshold = calibrator.threshold();
          setCalibrating(false);
        }
        return;
      }
      const utterance = detector.push(frame);
      callbacks.current.onLevel?.(detector.level);
      if (utterance) emit(utterance, sampleRate, false);
    };

    handles.current = { context, stream, nodes: [source, processor, mute] };
    if (context.state === 'suspended') await context.resume().catch(() => undefined);
    setActive(true);
    return true;
  }, [emit]);

  const beginPushToTalk = useCallback(async () => {
    if (!handles.current && !(await start())) return;
    ptt.current = { frames: [], samples: 0 };
    setPushToTalk(true);
  }, [start]);

  const endPushToTalk = useCallback(() => {
    const held = ptt.current;
    const h = handles.current;
    ptt.current = null;
    setPushToTalk(false);
    if (!held || !h || held.samples === 0) return;
    const samples = new Float32Array(held.samples);
    let offset = 0;
    for (const frame of held.frames) {
      samples.set(frame, offset);
      offset += frame.length;
    }
    // A tap with nothing said should not reach speech recognition.
    if (rms(samples) < 0.004) return;
    emit(samples, h.context.sampleRate, true);
  }, [emit]);
  const endPushToTalkRef = useRef(endPushToTalk);
  endPushToTalkRef.current = endPushToTalk;

  /** Stop listening while JARVIS thinks or speaks, so it never hears itself. */
  const setPaused = useCallback((value: boolean) => {
    paused.current = value;
  }, []);

  useEffect(() => stop, [stop]);

  return {
    active,
    calibrating,
    pushToTalk,
    error,
    start,
    stop,
    setPaused,
    beginPushToTalk,
    endPushToTalk,
  };
}
