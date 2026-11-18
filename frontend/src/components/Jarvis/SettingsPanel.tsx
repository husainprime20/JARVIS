import { useEffect, useState } from 'react';
import { Eye, EyeOff, KeyRound, Loader2, Trash2 } from 'lucide-react';
import {
  friendlyError,
  getKeyStatus,
  getSettings,
  removeApiKey,
  saveSettings,
  setApiKey,
  type KeyStatus,
  type Settings,
} from '../../lib/jarvis/api';

const PROVIDERS: Array<{ id: string; label: string; slot: string; hint: string; url: string }> = [
  {
    id: 'gemini',
    label: 'Google Gemini (free)',
    slot: 'GEMINI_API_KEY',
    hint: 'Free key from Google AI Studio.',
    url: 'https://aistudio.google.com/api-keys',
  },
];

const GEMINI_MODELS = [
  'gemini-3.5-flash-lite',
  'gemini-3.5-flash',
  'gemini-2.5-pro',
];

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [keys, setKeys] = useState<KeyStatus[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const refresh = async () => {
    try {
      const [s, k] = await Promise.all([getSettings(), getKeyStatus()]);
      setSettings(s);
      setKeys(k);
    } catch (e) {
      setError(friendlyError(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const hasKey = (slot: string) => keys.some((k) => k.name === slot && k.present);

  const update = async (patch: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    setBusy(true);
    setError('');
    try {
      await saveSettings(next);
      setMessage('Saved.');
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const saveKey = async (slot: string) => {
    const value = (drafts[slot] ?? '').trim();
    if (!value) return;
    setBusy(true);
    setError('');
    try {
      await setApiKey(slot, value);
      setDrafts((d) => ({ ...d, [slot]: '' }));
      setMessage('Key saved securely on this PC.');
      await refresh();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const deleteKey = async (slot: string) => {
    setBusy(true);
    setError('');
    try {
      await removeApiKey(slot);
      setMessage('Key removed.');
      await refresh();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!settings) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="animate-spin" style={{ color: 'var(--jarvis-cyan)' }} />
      </div>
    );
  }

  const engine = settings.engine;

  return (
    <div className="jarvis-scroll h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-xl">
        <div className="mb-6 flex items-center justify-between">
          <h2
            className="text-sm font-semibold tracking-[0.3em] uppercase"
            style={{ color: 'var(--jarvis-cyan)' }}
          >
            Settings
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-xs tracking-widest uppercase"
            style={{
              border: '1px solid rgba(56,189,248,0.3)',
              color: 'var(--jarvis-cyan)',
            }}
          >
            Back
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="mb-4 rounded-xl px-4 py-3 text-sm"
            style={{
              background: 'rgba(127,29,29,0.35)',
              border: '1px solid rgba(248,113,113,0.4)',
              color: '#fecaca',
            }}
          >
            {error}
          </div>
        )}
        {message && !error && (
          <div
            className="mb-4 rounded-xl px-4 py-3 text-sm"
            style={{
              background: 'rgba(6,78,59,0.35)',
              border: '1px solid rgba(52,211,153,0.35)',
              color: '#a7f3d0',
            }}
          >
            {message}
          </div>
        )}

        {/* Brain */}
        <section className="mb-8">
          <h3 className="mb-3 text-xs font-semibold tracking-[0.25em] uppercase" style={{ color: 'var(--jarvis-cyan-dim)' }}>
            Brain
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => void update({ engine: 'gemini' })}
              className="rounded-xl px-4 py-3 text-left"
              style={{
                border: `1px solid ${engine === 'gemini' ? 'var(--jarvis-cyan)' : 'rgba(56,189,248,0.2)'}`,
                background: engine === 'gemini' ? 'rgba(14,116,144,0.3)' : 'rgba(8,47,73,0.2)',
                color: '#e0f2fe',
              }}
            >
              <div className="text-sm font-semibold">Cloud API key</div>
              <div className="mt-0.5 text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                Default. Fast, light, nothing to download.
              </div>
            </button>
            <button
              type="button"
              onClick={() => void update({ engine: 'ollama' })}
              className="rounded-xl px-4 py-3 text-left"
              style={{
                border: `1px solid ${engine === 'ollama' ? 'var(--jarvis-cyan)' : 'rgba(56,189,248,0.2)'}`,
                background: engine === 'ollama' ? 'rgba(14,116,144,0.3)' : 'rgba(8,47,73,0.2)',
                color: '#e0f2fe',
              }}
            >
              <div className="text-sm font-semibold">Local Ollama</div>
              <div className="mt-0.5 text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                Optional. Fully offline, needs Ollama running.
              </div>
            </button>
          </div>

          {engine === 'gemini' && (
            <label className="mt-4 block text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
              Gemini model
              <select
                value={settings.gemini_model}
                onChange={(e) => void update({ gemini_model: e.target.value })}
                className="mt-1.5 block w-full rounded-lg px-3 py-2 text-sm"
                style={{
                  background: 'rgba(8,47,73,0.4)',
                  border: '1px solid rgba(56,189,248,0.25)',
                  color: '#e0f2fe',
                }}
              >
                {GEMINI_MODELS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </label>
          )}

          {engine === 'ollama' && (
            <div className="mt-4 grid gap-3">
              <label className="block text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                Ollama address
                <input
                  value={settings.ollama_host}
                  onChange={(e) => setSettings({ ...settings, ollama_host: e.target.value })}
                  onBlur={() => void update({ ollama_host: settings.ollama_host })}
                  className="mt-1.5 block w-full rounded-lg px-3 py-2 text-sm"
                  style={{
                    background: 'rgba(8,47,73,0.4)',
                    border: '1px solid rgba(56,189,248,0.25)',
                    color: '#e0f2fe',
                  }}
                />
              </label>
              <label className="block text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                Ollama model
                <input
                  value={settings.ollama_model}
                  onChange={(e) => setSettings({ ...settings, ollama_model: e.target.value })}
                  onBlur={() => void update({ ollama_model: settings.ollama_model })}
                  className="mt-1.5 block w-full rounded-lg px-3 py-2 text-sm"
                  style={{
                    background: 'rgba(8,47,73,0.4)',
                    border: '1px solid rgba(56,189,248,0.25)',
                    color: '#e0f2fe',
                  }}
                />
              </label>
            </div>
          )}
        </section>

        {/* API keys */}
        <section className="mb-8">
          <h3 className="mb-3 text-xs font-semibold tracking-[0.25em] uppercase" style={{ color: 'var(--jarvis-cyan-dim)' }}>
            API keys
          </h3>
          <p className="mb-4 text-xs leading-relaxed" style={{ color: 'var(--jarvis-cyan-dim)' }}>
            Keys are stored in Windows' secure credential store on this PC only — never in a file,
            never sent anywhere except the provider.
          </p>
          <div className="grid gap-3">
            {PROVIDERS.map((p) => (
              <div
                key={p.slot}
                className="rounded-xl px-4 py-3"
                style={{
                  border: '1px solid rgba(56,189,248,0.18)',
                  background: 'rgba(8,47,73,0.2)',
                }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <KeyRound size={14} style={{ color: 'var(--jarvis-cyan)' }} />
                    <span className="text-sm font-medium" style={{ color: '#e0f2fe' }}>{p.label}</span>
                  </div>
                  <span
                    className="rounded-full px-2 py-0.5 text-[10px] tracking-widest uppercase"
                    style={{
                      background: hasKey(p.slot) ? 'rgba(6,78,59,0.5)' : 'rgba(71,85,105,0.4)',
                      color: hasKey(p.slot) ? '#6ee7b7' : '#94a3b8',
                    }}
                  >
                    {hasKey(p.slot) ? 'Saved' : 'Not saved'}
                  </span>
                </div>
                <p className="mt-1 text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
                  {p.hint}{' '}
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: 'var(--jarvis-cyan)', textDecoration: 'underline' }}
                  >
                    Get a key
                  </a>
                </p>
                <div className="mt-2 flex gap-2">
                  <div className="relative flex-1">
                    <input
                      type={visible[p.slot] ? 'text' : 'password'}
                      value={drafts[p.slot] ?? ''}
                      onChange={(e) => setDrafts((d) => ({ ...d, [p.slot]: e.target.value }))}
                      placeholder={hasKey(p.slot) ? '•••••••• (saved)' : 'Paste key'}
                      autoComplete="off"
                      spellCheck={false}
                      className="w-full rounded-lg px-3 py-2 pr-9 text-sm"
                      style={{
                        background: 'rgba(2,6,23,0.6)',
                        border: '1px solid rgba(56,189,248,0.25)',
                        color: '#e0f2fe',
                      }}
                    />
                    <button
                      type="button"
                      aria-label="Show key"
                      onClick={() => setVisible((v) => ({ ...v, [p.slot]: !v[p.slot] }))}
                      className="absolute top-1/2 right-2 -translate-y-1/2"
                      style={{ color: 'var(--jarvis-cyan-dim)' }}
                    >
                      {visible[p.slot] ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                  <button
                    type="button"
                    disabled={busy || !(drafts[p.slot] ?? '').trim()}
                    onClick={() => void saveKey(p.slot)}
                    className="rounded-lg px-3 py-2 text-xs font-semibold tracking-wider uppercase disabled:opacity-40"
                    style={{ background: 'var(--jarvis-cyan)', color: '#082f49' }}
                  >
                    Save
                  </button>
                  {hasKey(p.slot) && (
                    <button
                      type="button"
                      aria-label={`Remove ${p.label} key`}
                      disabled={busy}
                      onClick={() => void deleteKey(p.slot)}
                      className="rounded-lg px-2.5 py-2 disabled:opacity-40"
                      style={{
                        border: '1px solid rgba(248,113,113,0.4)',
                        color: '#fca5a5',
                      }}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* You */}
        <section>
          <h3 className="mb-3 text-xs font-semibold tracking-[0.25em] uppercase" style={{ color: 'var(--jarvis-cyan-dim)' }}>
            You
          </h3>
          <label className="block text-xs" style={{ color: 'var(--jarvis-cyan-dim)' }}>
            What should JARVIS call you?
            <input
              value={settings.user_name}
              onChange={(e) => setSettings({ ...settings, user_name: e.target.value })}
              onBlur={() => void update({ user_name: settings.user_name })}
              placeholder="sir"
              className="mt-1.5 block w-full rounded-lg px-3 py-2 text-sm"
              style={{
                background: 'rgba(8,47,73,0.4)',
                border: '1px solid rgba(56,189,248,0.25)',
                color: '#e0f2fe',
              }}
            />
          </label>
          <label className="mt-4 flex items-center justify-between text-sm" style={{ color: '#e0f2fe' }}>
            <span>Wake word ("Jarvis")</span>
            <input
              type="checkbox"
              checked={settings.wake_word}
              onChange={(e) => void update({ wake_word: e.target.checked })}
              className="h-4 w-4"
            />
          </label>
          <label className="mt-3 flex items-center justify-between text-sm" style={{ color: '#e0f2fe' }}>
            <span>Speak replies aloud</span>
            <input
              type="checkbox"
              checked={settings.voice}
              onChange={(e) => void update({ voice: e.target.checked })}
              className="h-4 w-4"
            />
          </label>
        </section>
      </div>
    </div>
  );
}
