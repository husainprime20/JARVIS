/**
 * The JARVIS API layer. Everything here calls the Rust core through Tauri's
 * `invoke` — there is no Python server and no HTTP backend any more.
 */

export interface Settings {
  engine: string; // "gemini" (default) or "ollama"
  gemini_model: string;
  ollama_host: string;
  ollama_model: string;
  user_name: string;
  wake_word: boolean;
  voice: boolean;
}

export interface Reply {
  text: string;
  engine: string;
  model: string;
}

export interface KeyStatus {
  name: string;
  present: boolean;
}

export interface Health {
  engine: string;
  model: string;
  has_key: boolean;
  ready: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

// Legacy HUD component type retained while the non-JARVIS analytics pages are
// being migrated away from the Python server.
export interface JarvisStatus {
  engine: string; model: string; agent: string; voice: string; wake_word: string;
  jarvis_mode?: boolean;
  honorific?: string;
  tools: string[];
  stats?: any;
  [key: string]: any;
}

export interface LegacyChatMessage { role: 'user' | 'assistant'; content: string }

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
  return tauriInvoke<T>(cmd, args);
}

export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export async function chat(message: string): Promise<Reply> {
  return invoke<Reply>('chat', { message });
}

export async function transcribeAudio(audioBase64: string, mimeType: string): Promise<string> {
  return invoke<string>('transcribe_audio', { audioBase64, mimeType });
}

export async function clearHistory(): Promise<void> {
  return invoke<void>('clear_history');
}

export async function getSettings(): Promise<Settings> {
  return invoke<Settings>('get_settings');
}

export async function saveSettings(settings: Settings): Promise<void> {
  return invoke<void>('save_settings', { settings });
}

export async function setApiKey(name: string, value: string): Promise<void> {
  return invoke<void>('set_api_key', { name, value });
}

export async function removeApiKey(name: string): Promise<void> {
  return invoke<void>('remove_api_key', { name });
}

export async function getKeyStatus(): Promise<KeyStatus[]> {
  return invoke<KeyStatus[]>('key_status');
}

export async function getHealth(): Promise<Health> {
  return invoke<Health>('health');
}

export async function getMemory(): Promise<[string, string][]> {
  return invoke<[string, string][]>('get_memory');
}

export async function forgetMemory(key: string): Promise<void> {
  return invoke<void>('forget_memory', { key });
}

export async function runTool(name: string, args: Record<string, unknown> = {}): Promise<string> {
  return invoke<string>('run_tool', { name, args });
}

export async function toggleOverlay(): Promise<boolean> {
  return invoke<boolean>('toggle_overlay');
}

export async function focusMain(): Promise<void> {
  return invoke<void>('focus_main');
}

export async function quit(): Promise<void> {
  return invoke<void>('quit');
}

/** Human-readable version of any error the Rust core throws. */
export function friendlyError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return 'Something went wrong. Please try again.';
}
