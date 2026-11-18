import { useEffect, useRef } from 'react';
import type { VoiceState } from '../../lib/jarvis/voice';

interface Props {
  state: VoiceState;
  /** Ref holding a 0..1 activity level, updated by the parent. */
  levelRef: React.MutableRefObject<number>;
}

/**
 * A row of bars that dance while JARVIS listens, thinks or speaks. Purely
 * decorative — driven by a level ref so it never re-renders the tree.
 */
export function VoiceVisualizer({ state, levelRef }: Props) {
  const barsRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    let raf = 0;
    const bars = 28;
    const tick = () => {
      const el = barsRef.current;
      if (el) {
        const level = levelRef.current;
        const s = stateRef.current;
        const base = s === 'idle' ? 0.06 : s === 'thinking' ? 0.35 : 0.55;
        for (let i = 0; i < bars; i++) {
          const bar = el.children[i] as HTMLElement | undefined;
          if (!bar) continue;
          const wave = Math.sin(i * 0.55 + performance.now() / 140) * 0.5 + 0.5;
          const h = Math.max(3, (base + level * wave) * 34);
          bar.style.height = `${h}px`;
          bar.style.opacity = `${0.35 + wave * 0.6}`;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levelRef]);

  return (
    <div
      ref={barsRef}
      aria-hidden
      className="flex h-10 items-center justify-center gap-[3px]"
    >
      {Array.from({ length: 28 }).map((_, i) => (
        <span
          key={i}
          className="w-[3px] rounded-full"
          style={{ background: 'var(--jarvis-cyan)', height: '4px' }}
        />
      ))}
    </div>
  );
}
