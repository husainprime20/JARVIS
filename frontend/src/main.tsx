import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import { JarvisPage } from './pages/JarvisPage';
import './index.css';
import './jarvis.css';

// The desktop app is the Rust-first JARVIS UI. Keep this entry intentionally
// small: no legacy app shell, Python API client, onboarding framework or
// analytics bundle is loaded in the Windows desktop runtime.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <JarvisPage />
    </ErrorBoundary>
  </StrictMode>,
);
