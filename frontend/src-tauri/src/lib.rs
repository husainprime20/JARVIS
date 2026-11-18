//! JARVIS — a self-contained Rust AI assistant.
//!
//! The whole brain lives here in Rust: it talks to Gemini (default) or Ollama
//! directly, runs tools on the PC, and remembers the user. There is no Python
//! backend and nothing to install on first launch, so the app starts fast and
//! stays light.

mod brain;
mod store;
mod tools;

use std::sync::Mutex;

use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// Conversation history kept in memory for the current session.
struct Session {
    history: Vec<brain::Message>,
}

impl Session {
    fn new() -> Self {
        let mut history = store::load_history();
        if history.len() > 40 {
            history.drain(0..history.len() - 40);
        }
        Self { history }
    }
}

#[derive(Serialize)]
struct KeyStatus {
    name: String,
    present: bool,
}

#[derive(Serialize)]
struct Health {
    engine: String,
    model: String,
    has_key: bool,
    ready: bool,
}

// ---------------------------------------------------------------------------
// Commands the frontend calls
// ---------------------------------------------------------------------------

/// Send a message to JARVIS and get a spoken answer back.
#[tauri::command]
async fn chat(
    state: tauri::State<'_, Mutex<Session>>,
    message: String,
) -> Result<brain::Reply, String> {
    let text = message.trim().to_string();
    if text.is_empty() {
        return Err("Say something first, sir.".to_string());
    }
    let history = {
        let session = state.lock().map_err(|_| "JARVIS is busy.".to_string())?;
        session.history.clone()
    };
    let reply = brain::chat(&history, &text).await?;
    {
        let mut session = state.lock().map_err(|_| "JARVIS is busy.".to_string())?;
        session.history.push(brain::Message {
            role: "user".to_string(),
            text: text.clone(),
        });
        session.history.push(brain::Message {
            role: "assistant".to_string(),
            text: reply.text.clone(),
        });
        // Keep the last 20 turns so prompts stay small and fast.
        if session.history.len() > 40 {
            let excess = session.history.len() - 40;
            session.history.drain(0..excess);
        }
        // The live turn succeeds even if local disk is temporarily unavailable;
        // the current session remains intact and a later turn retries persistence.
        let _ = store::save_history(&session.history);
    }
    Ok(reply)
}

/// Transcribe an audio recording using the active Gemini key.
#[tauri::command]
async fn transcribe_audio(audio_base64: String, mime_type: String) -> Result<String, String> {
    brain::transcribe_audio(&audio_base64, &mime_type).await
}

/// Return the local conversation so the redesigned UI can resume after restart.
#[tauri::command]
fn get_history(state: tauri::State<'_, Mutex<Session>>) -> Result<Vec<brain::Message>, String> {
    let session = state.lock().map_err(|_| "JARVIS is busy.".to_string())?;
    Ok(session.history.clone())
}

/// Clear the in-memory conversation.
#[tauri::command]
fn clear_history(state: tauri::State<'_, Mutex<Session>>) -> Result<(), String> {
    let mut session = state.lock().map_err(|_| "JARVIS is busy.".to_string())?;
    session.history.clear();
    store::save_history(&session.history)

}

#[tauri::command]
fn get_settings() -> store::Settings {
    store::load_settings()
}

#[tauri::command]
fn save_settings(settings: store::Settings) -> Result<(), String> {
    store::save_settings(&settings)
}

#[tauri::command]
fn set_api_key(name: String, value: String) -> Result<(), String> {
    if !store::KEY_SLOTS.contains(&name.as_str()) {
        return Err(format!("Unknown key slot: {name}"));
    }
    store::set_key(&name, &value)
}

#[tauri::command]
fn remove_api_key(name: String) -> Result<(), String> {
    store::delete_key(&name)
}

#[tauri::command]
fn key_status() -> Vec<KeyStatus> {
    store::key_status()
        .into_iter()
        .map(|(name, present)| KeyStatus { name, present })
        .collect()
}

/// Is the brain ready to answer? Used by the HUD to show a clear state.
#[tauri::command]
async fn health() -> Health {
    let settings = store::load_settings();
    let engine = settings.engine.clone();
    let (model, has_key, ready) = if engine == "ollama" {
        let host = settings.ollama_host.trim().trim_end_matches('/');
        let host = if host.is_empty() { "http://127.0.0.1:11434" } else { host };
        let ready = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
        {
            Ok(client) => client
                .get(format!("{host}/api/tags"))
                .send()
                .await
                .map(|response| response.status().is_success())
                .unwrap_or(false),
            Err(_) => false,
        };
        (settings.ollama_model.clone(), true, ready)
    } else {
        let has = store::first_key(&["GEMINI_API_KEY", "GOOGLE_API_KEY"]).is_some();
        (settings.gemini_model.clone(), has, has)
    };
    Health { engine, model, has_key, ready }
}

#[tauri::command]
fn get_memory() -> Vec<(String, String)> {
    let mut entries: Vec<(String, String)> = store::load_memory()
        .into_iter()
        .map(|(k, v)| (k, v))
        .collect();
    entries.sort();
    entries
}

#[tauri::command]
fn forget_memory(key: String) -> Result<(), String> {
    let mut memory = store::load_memory();
    memory.remove(&key);
    store::save_memory(&memory)
}

/// Open an app by name (used by the HUD buttons and quick actions).
#[tauri::command]
async fn run_tool(name: String, args: serde_json::Value) -> String {
    tools::execute(&name, &args).await
}

/// Show or hide the always-on overlay orb.
#[tauri::command]
fn toggle_overlay(app: tauri::AppHandle) -> Result<bool, String> {
    let Some(window) = app.get_webview_window("overlay") else {
        return Err("Overlay window is not available.".to_string());
    };
    let visible = window.is_visible().unwrap_or(false);
    if visible {
        window.hide().map_err(|e| e.to_string())?;
        Ok(false)
    } else {
        window.show().map_err(|e| e.to_string())?;
        window.set_focus().map_err(|e| e.to_string())?;
        Ok(true)
    }
}

/// Bring the main HUD to the front (or open it).
#[tauri::command]
fn focus_main(app: tauri::AppHandle) -> Result<(), String> {
    let Some(window) = app.get_webview_window("main") else {
        return Err("Main window is not available.".to_string());
    };
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())?;
    Ok(())
}

/// Quit JARVIS completely.
#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

// ---------------------------------------------------------------------------
// App setup
// ---------------------------------------------------------------------------

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            focus_main(app.to_owned()).ok();
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(Mutex::new(Session::new()))
        .setup(|app| {
            // System tray: JARVIS lives in the tray so it is always there.
            let quit_item = MenuItem::with_id(app, "quit", "Quit JARVIS", true, None::<&str>)?;
            let show_item = MenuItem::with_id(app, "show", "Show JARVIS", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;
            let _tray = TrayIconBuilder::with_id("main")
                .tooltip("JARVIS")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "quit" => app.exit(0),
                    "show" => {
                        focus_main(app.to_owned()).ok();
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click {
                        button: tauri::tray::MouseButton::Left,
                        button_state: tauri::tray::MouseButtonState::Up,
                        ..
                    } = event
                    {
                        focus_main(tray.app_handle().to_owned()).ok();
                    }
                })
                .build(app)?;

            // Global hotkey: Ctrl+Alt+J summons JARVIS from anywhere.
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyJ);
            app.global_shortcut().on_shortcut(shortcut, |app, _sc, event| {
                if event.state == ShortcutState::Pressed {
                    focus_main(app.to_owned()).ok();
                }
            })?;

            // Closing the main window hides it to the tray instead of quitting,
            // so JARVIS stays "everywhere" until the user quits from the tray.
            if let Some(main) = app.get_webview_window("main") {
                let window = main.clone();
                main.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = window.hide();
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            chat,
            transcribe_audio,
            clear_history,
            get_history,
            get_settings,
            save_settings,
            set_api_key,
            remove_api_key,
            key_status,
            health,
            get_memory,
            forget_memory,
            run_tool,
            toggle_overlay,
            focus_main,
            quit
        ])
        .run(tauri::generate_context!())
        .expect("error while running JARVIS");
}
