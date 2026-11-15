import { Activity, BatteryCharging, Battery, Clock, Cpu, HardDrive, MemoryStick } from 'lucide-react';
import type { ReactNode } from 'react';
import type { JarvisStatus } from '../../lib/jarvis/api';

/** Gauge colour: calm cyan, amber when busy, red when nearly full. */
export function gaugeColor(percent: number): string {
  if (percent >= 90) return '#f87171';
  if (percent >= 70) return '#f5a524';
  return '#22d3ee';
}

function Gauge({
  icon,
  label,
  percent,
  detail,
  invert = false,
}: {
  icon: ReactNode;
  label: string;
  percent: number;
  detail: string;
  /** For battery, where low is the warning. */
  invert?: boolean;
}) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
        {icon}
        <span className="uppercase tracking-[0.18em]">{label}</span>
        <span className="ml-auto hud-mono" style={{ color: 'var(--color-text)' }}>
          {Math.round(clamped)}%
        </span>
      </div>
      <div className="jarvis-meter" role="meter" aria-label={label} aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${clamped}%`, backgroundColor: gaugeColor(invert ? 100 - clamped : clamped) }} />
      </div>
      <div className="text-[11px] hud-mono" style={{ color: 'var(--color-text-tertiary)' }}>
        {detail}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="uppercase tracking-[0.18em]" style={{ color: 'var(--color-text-tertiary)' }}>
        {label}
      </span>
      <span className="hud-mono truncate text-right" style={{ color: 'var(--color-text)' }}>
        {value}
      </span>
    </div>
  );
}

export function StatsPanel({ status }: { status: JarvisStatus | null }) {
  const stats = status?.stats;
  return (
    <div className="flex flex-col gap-4">
      <section className="hud-panel p-4 flex flex-col gap-4" aria-label="System">
        <div className="flex items-center gap-2">
          <Activity size={14} style={{ color: '#22d3ee' }} />
          <h2 className="text-xs uppercase tracking-[0.25em] hud-title">System</h2>
          {stats?.os && (
            <span className="ml-auto text-[11px] hud-mono" style={{ color: 'var(--color-text-tertiary)' }}>
              {stats.os}
            </span>
          )}
        </div>
        {!stats && <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Waiting for telemetry…</p>}
        {stats?.error && (
          <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            Stats unavailable: {stats.error}
          </p>
        )}
        {stats?.cpu && (
          <Gauge
            icon={<Cpu size={13} />}
            label="CPU"
            percent={stats.cpu.percent}
            detail={`${stats.cpu.logical_cores} logical cores`}
          />
        )}
        {stats?.memory && (
          <Gauge
            icon={<MemoryStick size={13} />}
            label="Memory"
            percent={stats.memory.percent}
            detail={`${stats.memory.used_gb} of ${stats.memory.total_gb} GB`}
          />
        )}
        {stats?.disk && (
          <Gauge
            icon={<HardDrive size={13} />}
            label={`Drive ${stats.disk.path.replace(/\\$/, '')}`}
            percent={stats.disk.percent}
            detail={`${stats.disk.used_gb} of ${stats.disk.total_gb} GB`}
          />
        )}
        {stats?.battery && (
          <Gauge
            icon={stats.battery.plugged_in ? <BatteryCharging size={13} /> : <Battery size={13} />}
            label="Battery"
            percent={stats.battery.percent}
            invert
            detail={
              stats.battery.plugged_in
                ? 'Charging'
                : stats.battery.time_left
                  ? `${stats.battery.time_left} left`
                  : 'On battery'
            }
          />
        )}
        {stats?.uptime && (
          <div className="flex items-center gap-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
            <Clock size={12} />
            <span>Up {stats.uptime}</span>
          </div>
        )}
      </section>

      <section className="hud-panel p-4 flex flex-col gap-2.5" aria-label="JARVIS core">
        <h2 className="text-xs uppercase tracking-[0.25em] hud-title mb-1">Core</h2>
        <Row label="Brain" value={status?.model || '—'} />
        <Row label="Engine" value={status?.engine || '—'} />
        <Row label="Agent" value={status?.agent || '—'} />
        <Row label="Voice" value={status?.voice || '—'} />
        <Row
          label="Wake word"
          value={status ? `“${status.wake_word.charAt(0).toUpperCase()}${status.wake_word.slice(1)}”` : '—'}
        />
        {status && status.tools.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-2">
            {status.tools.map((tool) => (
              <span
                key={tool}
                className="text-[10px] hud-mono px-1.5 py-0.5 rounded"
                style={{
                  color: '#22d3ee',
                  background: 'rgba(34, 211, 238, 0.08)',
                  border: '1px solid rgba(34, 211, 238, 0.2)',
                }}
              >
                {tool}
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
