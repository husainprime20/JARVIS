import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { ArcReactor } from './components/Jarvis/ArcReactor';
import { focusMain, toggleOverlay } from './lib/jarvis/api';
import type { VoiceState } from './lib/jarvis/voice';
import './index.css';
import './jarvis.css';

function Overlay() {
  const [state, setState] = useState<VoiceState>('idle');
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') void toggleOverlay();
      if (event.key === 'Enter') void focusMain();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  return (
    <div className="jarvis-overlay">
      <div className="overlay-topline"><span>JARVIS</span><button title="Hide overlay" onClick={() => void toggleOverlay()}>×</button></div>
      <ArcReactor state={state} level={0.3} onClick={() => { setState('thinking'); void focusMain(); }} />
      <button className="overlay-open" onClick={() => void focusMain()}>OPEN ASSISTANT</button>
      <span className="overlay-hint">CTRL + ALT + J · ESC TO HIDE</span>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Overlay />);
