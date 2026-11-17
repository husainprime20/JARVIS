//! Persistent storage for JARVIS: settings, memory and API keys.
//!
//! Settings and memory live as JSON in the app-data directory. API keys live
//! in the OS credential store (Windows Credential Manager) via `keyring`, so
//! they are never written to disk in plain text.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

/// Service name used for every key in the OS credential store.
const KEY_SERVICE: &str = "JARVIS API Keys";

/// The API-key slots JARVIS understands. The first entry for a provider is the
/// one the Settings UI writes; the rest are accepted as aliases.
pub const KEY_SLOTS: &[&str] = &[
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
];

/// App settings. `engine` is `"gemini"` (the default) or `"ollama"`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub engine: String,
    pub gemini_model: String,
    pub ollama_host: String,
    pub ollama_model: String,
    pub user_name: String,
    pub wake_word: bool,
    pub voice: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            // A cloud API key is the default brain: no Ollama, no downloads.
            engine: "gemini".to_string(),
            gemini_model: "gemini-2.5-flash".to_string(),
            ollama_host: "http://127.0.0.1:11434".to_string(),
            ollama_model: "llama3.1:8b".to_string(),
            user_name: String::new(),
            wake_word: true,
            voice: true,
        }
    }
}

/// Directory that holds settings.json and memory.json.
pub fn data_dir() -> PathBuf {
    if let Some(dir) = dirs_data_dir() {
        return dir.join("JARVIS");
    }
    PathBuf::from(".jarvis")
}

fn dirs_data_dir() -> Option<PathBuf> {
    // Tauri sets the app-data dir; fall back to the OS default so the core
    // also works when run outside the desktop shell (tests, tools).
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))
}

fn read_json<T: for<'de> Deserialize<'de> + Default>(name: &str) -> T {
    let path = data_dir().join(name);
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn write_json<T: Serialize>(name: &str, value: &T) -> Result<(), String> {
    let dir = data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("Could not create data folder: {e}"))?;
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    std::fs::write(dir.join(name), text).map_err(|e| format!("Could not save {name}: {e}"))?;
    Ok(())
}

pub fn load_settings() -> Settings {
    read_json("settings.json")
}

pub fn save_settings(settings: &Settings) -> Result<(), String> {
    write_json("settings.json", settings)
}

/// Long-term memory: a flat map of facts JARVIS remembers about the user.
pub type Memory = HashMap<String, String>;

pub fn load_memory() -> Memory {
    read_json("memory.json")
}

pub fn save_memory(memory: &Memory) -> Result<(), String> {
    write_json("memory.json", memory)
}

// ---------------------------------------------------------------------------
// Secure key storage (OS credential store)
// ---------------------------------------------------------------------------

pub fn get_key(name: &str) -> Option<String> {
    let entry = keyring::Entry::new(KEY_SERVICE, name).ok()?;
    match entry.get_password() {
        Ok(value) if !value.trim().is_empty() => Some(value),
        _ => None,
    }
}

pub fn set_key(name: &str, value: &str) -> Result<(), String> {
    let entry =
        keyring::Entry::new(KEY_SERVICE, name).map_err(|e| format!("Key store unavailable: {e}"))?;
    entry
        .set_password(value.trim())
        .map_err(|e| format!("Could not save the key: {e}"))
}

pub fn delete_key(name: &str) -> Result<(), String> {
    let entry =
        keyring::Entry::new(KEY_SERVICE, name).map_err(|e| format!("Key store unavailable: {e}"))?;
    entry
        .delete_credential()
        .map_err(|e| format!("Could not remove the key: {e}"))
}

/// Which key slots currently hold a value (name, present). Never returns the
/// key material itself, so the frontend can show status without seeing secrets.
pub fn key_status() -> Vec<(String, bool)> {
    KEY_SLOTS
        .iter()
        .map(|name| ((*name).to_string(), get_key(name).is_some()))
        .collect()
}

/// The first saved key for a provider, checking its slots in order.
pub fn first_key(slots: &[&str]) -> Option<String> {
    slots.iter().find_map(|name| get_key(name))
}
