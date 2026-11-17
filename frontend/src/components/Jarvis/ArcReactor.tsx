import type { VoiceState } from '../../lib/jarvis/voice';

export type ReactorPhase = 'offline' | 'standby' | 'idle' | 'listening' | 'hearing' | 'thinking' | 'speaking';
interface Props { state: VoiceState; level: number; onClick?: () => void }
const LABEL: Record<VoiceState, string> = { idle: 'At your service', listening: 'Listening for “Jarvis”', hearing: 'Yes, sir?', thinking: 'Thinking', speaking: 'Speaking' };

export function ArcReactor({ state, level, onClick }: Props) {
  const active = state !== 'idle';
  const speed = state === 'thinking' ? 2.2 : state === 'speaking' ? 1.4 : 1;
  const glow = 0.35 + Math.min(1, Math.max(0, level)) * 0.65;
  return (
    <button type="button" onClick={onClick} aria-label={`JARVIS core: ${LABEL[state]}. Activate to talk.`} className="group relative block h-64 w-64 shrink-0 cursor-pointer outline-none" style={{ background: 'transparent', border: 'none' }}>
      <div className="absolute inset-0 rounded-full" style={{ background: 'radial-gradient(circle, rgba(56,189,248,.28), rgba(56,189,248,.08) 45%, transparent 70%)', opacity: glow, transition: 'opacity 300ms ease' }} />
      <svg viewBox="0 0 200 200" className="absolute inset-0 h-full w-full">
        <defs><radialGradient id="jarvis-core" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor="#e0f2fe"/><stop offset="45%" stopColor="#7dd3fc"/><stop offset="100%" stopColor="#0369a1"/></radialGradient><linearGradient id="jarvis-ring"><stop stopColor="#38bdf8"/><stop offset="100%" stopColor="#0c4a6e"/></linearGradient></defs>
        <g style={{ transformOrigin: '100px 100px', animation: `jarvis-spin ${26 / speed}s linear infinite` }}><circle cx="100" cy="100" r="94" fill="none" stroke="url(#jarvis-ring)" strokeWidth="1.5" strokeDasharray="4 10" opacity={active ? .9 : .5}/></g>
        <g style={{ transformOrigin: '100px 100px', animation: `jarvis-spin-rev ${18 / speed}s linear infinite` }}>{Array.from({ length: 48 }, (_, i) => { const a = i / 48 * Math.PI * 2; const r1 = i % 4 === 0 ? 74 : 79; return <line key={i} x1={100 + Math.cos(a) * r1} y1={100 + Math.sin(a) * r1} x2={100 + Math.cos(a) * 84} y2={100 + Math.sin(a) * 84} stroke="#38bdf8" strokeWidth={i % 4 === 0 ? 2 : 1} opacity={active ? .85 : .4}/>; })}</g>
        <circle cx="100" cy="100" r="68" fill="none" stroke="#0ea5e9" opacity={active ? .7 : .35}/><circle cx="100" cy="100" r={34 + glow * 6} fill="url(#jarvis-core)" opacity={.55 + glow * .45} style={{ transition: 'r 300ms ease' }}/><circle cx="100" cy="100" r="20" fill="#f0f9ff" opacity={.85 * glow}/>
      </svg>
      <span className="absolute inset-x-0 -bottom-7 text-center text-[11px] font-medium tracking-[.25em] uppercase" style={{ color: 'var(--jarvis-cyan-dim)' }}>{LABEL[state]}</span>
    </button>
  );
}
