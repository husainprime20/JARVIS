import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../lib/jarvis/api';

interface Props {
  messages: ChatMessage[];
  /** The partial transcript while the user is speaking. */
  draft: string;
  thinking: boolean;
}

export function ChatPanel({ messages, draft, thinking }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, draft, thinking]);

  return (
    <div
      ref={scrollRef}
      className="jarvis-scroll flex-1 overflow-y-auto px-5 py-4"
      aria-live="polite"
    >
      {messages.length === 0 && !draft && !thinking && (
        <div className="flex h-full flex-col items-center justify-center text-center">
          <p
            className="max-w-sm text-sm leading-relaxed"
            style={{ color: 'var(--jarvis-cyan-dim)' }}
          >
            Say <span className="font-semibold" style={{ color: 'var(--jarvis-cyan)' }}>"Jarvis"</span>,
            then ask me anything — open an app, check the weather, tell the time, or just talk.
          </p>
        </div>
      )}

      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={msg.role === 'user' ? 'self-end' : 'self-start'}
            style={{ maxWidth: '85%' }}
          >
            <div
              className="rounded-2xl px-4 py-2.5 text-sm leading-relaxed"
              style={
                msg.role === 'user'
                  ? {
                      background: 'rgba(14,116,144,0.35)',
                      border: '1px solid rgba(56,189,248,0.35)',
                      color: '#e0f2fe',
                      borderBottomRightRadius: '6px',
                    }
                  : {
                      background: 'rgba(8,47,73,0.35)',
                      border: '1px solid rgba(56,189,248,0.18)',
                      color: '#bae6fd',
                      borderBottomLeftRadius: '6px',
                    }
              }
            >
              {msg.text}
            </div>
            <div
              className="mt-1 text-[10px] tracking-[0.2em] uppercase"
              style={{
                color: 'var(--jarvis-cyan-dim)',
                textAlign: msg.role === 'user' ? 'right' : 'left',
              }}
            >
              {msg.role === 'user' ? 'You' : 'JARVIS'}
            </div>
          </div>
        ))}

        {draft && (
          <div className="self-end" style={{ maxWidth: '85%' }}>
            <div
              className="rounded-2xl px-4 py-2.5 text-sm italic"
              style={{
                background: 'rgba(14,116,144,0.2)',
                border: '1px dashed rgba(56,189,248,0.4)',
                color: '#e0f2fe',
                borderBottomRightRadius: '6px',
              }}
            >
              {draft}…
            </div>
          </div>
        )}

        {thinking && (
          <div className="self-start">
            <div
              className="flex items-center gap-2 rounded-2xl px-4 py-3"
              style={{
                background: 'rgba(8,47,73,0.35)',
                border: '1px solid rgba(56,189,248,0.18)',
                borderBottomLeftRadius: '6px',
              }}
            >
              <span className="jarvis-dot" />
              <span className="jarvis-dot" style={{ animationDelay: '0.15s' }} />
              <span className="jarvis-dot" style={{ animationDelay: '0.3s' }} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
