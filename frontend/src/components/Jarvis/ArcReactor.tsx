import { useId, type Ref } from 'react';

export type ReactorPhase = 'offline' | 'standby' | 'listening' | 'hearing' | 'thinking' | 'speaking';

const CENTER = 150;
const TICKS = Array.from({ length: 72 }, (_, i) => i);
const COILS = Array.from({ length: 10 }, (_, i) => i);

/**
 * The HUD's animated arc reactor. Colour and spin speed follow `phase`
 * (see the .jarvis-reactor rules in index.css); the core group is exposed
 * through `coreRef` so the page can pulse it with the mic level without a
 * React re-render per audio frame.
 */
export function ArcReactor({
  phase,
  size = 300,
  coreRef,
}: {
  phase: ReactorPhase;
  size?: number;
  coreRef?: Ref<SVGGElement>;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const coreGradient = `jarvis-core-${uid}`;
  const haloGradient = `jarvis-halo-${uid}`;

  return (
    <div
      className="jarvis-reactor jarvis-glow"
      data-phase={phase}
      style={{ width: size, maxWidth: '78vw', aspectRatio: '1 / 1' }}
    >
      <svg viewBox="0 0 300 300" width="100%" height="100%" role="img" aria-label={`JARVIS is ${phase}`}>
        <defs>
          <radialGradient id={coreGradient}>
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="45%" stopColor="#ecfeff" />
            <stop offset="100%" stopColor="currentColor" />
          </radialGradient>
          <radialGradient id={haloGradient}>
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.9" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Outer scale: 72 ticks, every sixth one long. */}
        <g className="jarvis-ring jarvis-ring--slow" opacity={0.55}>
          {TICKS.map((i) => (
            <line
              key={i}
              x1={CENTER}
              y1={i % 6 === 0 ? 4 : 9}
              x2={CENTER}
              y2={16}
              stroke="currentColor"
              strokeWidth={i % 6 === 0 ? 2 : 1}
              transform={`rotate(${i * 5} ${CENTER} ${CENTER})`}
            />
          ))}
        </g>
        <circle cx={CENTER} cy={CENTER} r={128} fill="none" stroke="currentColor" strokeOpacity={0.3} />

        {/* Segmented ring, counter-rotating. */}
        <circle
          className="jarvis-ring jarvis-ring--reverse"
          cx={CENTER}
          cy={CENTER}
          r={114}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.75}
          strokeWidth={7}
          strokeDasharray="46 12 8 12"
        />
        <circle
          className="jarvis-ring"
          cx={CENTER}
          cy={CENTER}
          r={100}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.45}
          strokeWidth={1.5}
          strokeDasharray="2 6"
        />

        {/* Ten coil segments, like the Mark I reactor. */}
        <g>
          {COILS.map((i) => (
            <rect
              key={i}
              x={CENTER - 11}
              y={CENTER - 90}
              width={22}
              height={26}
              rx={3}
              fill="currentColor"
              fillOpacity={0.18}
              stroke="currentColor"
              strokeOpacity={0.85}
              strokeWidth={1.5}
              transform={`rotate(${i * 36} ${CENTER} ${CENTER})`}
            />
          ))}
        </g>
        <circle cx={CENTER} cy={CENTER} r={58} fill="none" stroke="currentColor" strokeWidth={2} strokeOpacity={0.9} />

        <circle className="jarvis-halo" cx={CENTER} cy={CENTER} r={56} fill={`url(#${haloGradient})`} />
        <g className="jarvis-core" ref={coreRef}>
          <circle cx={CENTER} cy={CENTER} r={34} fill={`url(#${coreGradient})`} />
          <circle cx={CENTER} cy={CENTER} r={22} fill="none" stroke="#ffffff" strokeOpacity={0.55} strokeWidth={1.5} />
        </g>
      </svg>
    </div>
  );
}
