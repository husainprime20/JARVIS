//! The brain: talks to Gemini (default) or Ollama, and runs the agent loop
//! that lets JARVIS call tools and then answer.
//!
//! Gemini is the default engine and supports function calling, so JARVIS can
//! open apps, check the weather and so on. Ollama is an optional local engine
//! (chat only) for users who enable it in Settings.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::store;
use crate::tools;

const GEMINI_BASE: &str = "https://generativelanguage.googleapis.com/v1beta/models";
const MAX_TOOL_ROUNDS: usize = 6;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String, // "user" or "assistant"
    pub text: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Reply {
    pub text: String,
    pub engine: String,
    pub model: String,
}

fn system_prompt(settings: &store::Settings) -> String {
    let name = if settings.user_name.trim().is_empty() {
        "sir".to_string()
    } else {
        settings.user_name.trim().to_string()
    };
    let memory = store::load_memory();
    let memory_text = if memory.is_empty() {
        String::new()
    } else {
        let mut lines: Vec<String> = memory
            .iter()
            .map(|(k, v)| format!("- {k}: {v}"))
            .collect();
        lines.sort();
        format!("\nWhat you remember about the user:\n{}", lines.join("\n"))
    };
    format!(
        "You are JARVIS, a calm and capable AI assistant in the style of Tony Stark's \
         butler. You speak to {name}. Keep answers short, clear and natural, as if \
         spoken aloud. Use the tools available to you to act on the user's PC: open \
         apps and websites, play YouTube, tell the time, check the weather, do maths \
         and remember facts. When a tool can answer something, call it rather than \
         guessing. Never invent results. If something fails, say so plainly and \
         suggest a fix.{memory_text}"
    )
}

/// Convert a short microphone recording into text using Gemini's audio input.
/// This is the Windows WebView fallback when its Web Speech API is unavailable.
pub async fn transcribe_audio(audio_base64: &str, mime_type: &str) -> Result<String, String> {
    if audio_base64.len() > 12_000_000 {
        return Err("That recording is too long. Please try a shorter voice message.".to_string());
    }
    let key = gemini_key()?;
    let settings = store::load_settings();
    let model = if settings.gemini_model.trim().is_empty() { "gemini-2.5-flash" } else { settings.gemini_model.trim() };
    let mime = match mime_type.split(';').next().unwrap_or("") {
        "audio/webm" | "audio/ogg" | "audio/wav" | "audio/mp4" | "audio/mpeg" => mime_type.split(';').next().unwrap_or("audio/webm"),
        _ => "audio/webm",
    };
    let body = json!({
        "contents": [{ "role": "user", "parts": [
            { "text": "Transcribe exactly the words spoken in this audio. Return only the transcript, with no commentary. If there is no intelligible speech, return an empty string." },
            { "inlineData": { "mimeType": mime, "data": audio_base64 } }
        ]}],
        "generationConfig": { "temperature": 0.0, "maxOutputTokens": 256 }
    });
    let value = gemini_once(&reqwest::Client::new(), model, &key, &body).await?;
    let (text, _) = parse_gemini_turn(&value);
    Ok(text.trim().to_string())
}

/// Run one turn: send the conversation to the chosen engine, let it call tools
/// as needed, and return the final spoken answer.
pub async fn chat(history: &[Message], user_text: &str) -> Result<Reply, String> {
    let settings = store::load_settings();
    match settings.engine.as_str() {
        "ollama" => chat_ollama(&settings, history, user_text).await,
        _ => chat_gemini(&settings, history, user_text).await,
    }
}

// ---------------------------------------------------------------------------
// Gemini
// ---------------------------------------------------------------------------

fn gemini_key() -> Result<String, String> {
    store::first_key(&["GEMINI_API_KEY", "GOOGLE_API_KEY"]).ok_or_else(|| {
        "No Gemini API key is saved. Open Settings and paste a free key from \
         https://aistudio.google.com/api-keys, then try again."
            .to_string()
    })
}

fn to_contents(history: &[Message], user_text: &str) -> Vec<Value> {
    let mut contents: Vec<Value> = Vec::new();
    for msg in history {
        let role = if msg.role == "assistant" { "model" } else { "user" };
        contents.push(json!({
            "role": role,
            "parts": [{ "text": msg.text }]
        }));
    }
    contents.push(json!({
        "role": "user",
        "parts": [{ "text": user_text }]
    }));
    contents
}

/// Gemini's REST schema is not full JSON Schema: it rejects fields such as
/// `additionalProperties` (including the snake_case spelling emitted by some
/// schema generators). Strip unsupported schema keywords recursively before
/// sending, so provider SDK/tool schema changes cannot break every chat turn.
fn gemini_compatible_schema(value: &mut Value) {
    match value {
        Value::Object(object) => {
            object.remove("additional_properties");
            object.remove("additionalProperties");
            for child in object.values_mut() {
                gemini_compatible_schema(child);
            }
        }
        Value::Array(items) => {
            for child in items {
                gemini_compatible_schema(child);
            }
        }
        _ => {}
    }
}

async fn gemini_once(
    client: &reqwest::Client,
    model: &str,
    key: &str,
    body: &Value,
) -> Result<Value, String> {
    let url = format!("{GEMINI_BASE}/{model}:generateContent");
    let mut compatible_body = body.clone();
    gemini_compatible_schema(&mut compatible_body);
    let response = client
        .post(&url)
        .query(&[("key", key)])
        .json(&compatible_body)
        .send()
        .await
        .map_err(|e| format!("I couldn't reach Google's servers: {e}"))?;
    let status = response.status();
    let value: Value = response
        .json()
        .await
        .map_err(|_| "Google sent back a reply I couldn't read.".to_string())?;
    if !status.is_success() {
        return Err(gemini_error(&value, status.as_u16()));
    }
    Ok(value)
}

fn gemini_error(value: &Value, status: u16) -> String {
    let message = value
        .get("error")
        .and_then(|e| e.get("message"))
        .and_then(|m| m.as_str())
        .unwrap_or("")
        .to_string();
    let lower = message.to_lowercase();
    if status == 400 && (lower.contains("api key") || lower.contains("api_key")) {
        "Google rejected the API key. Check the key in Settings — it may be mistyped or revoked.".to_string()
    } else if status == 429 || lower.contains("quota") || lower.contains("rate") {
        "I've hit the free Gemini limit for now. Wait a minute and ask again, or use a different key in Settings.".to_string()
    } else if status == 404 || lower.contains("not found") || lower.contains("not supported") {
        format!("That Gemini model isn't available. Pick another model in Settings. ({message})")
    } else if message.is_empty() {
        format!("Google returned an error (HTTP {status}).")
    } else {
        message
    }
}

/// Extract the text and function calls from one Gemini response.
fn parse_gemini_turn(value: &Value) -> (String, Vec<(String, Value)>) {
    let mut text = String::new();
    let mut calls: Vec<(String, Value)> = Vec::new();
    let parts = value
        .get("candidates")
        .and_then(|c| c.get(0))
        .and_then(|c| c.get("content"))
        .and_then(|c| c.get("parts"))
        .and_then(|p| p.as_array());
    if let Some(parts) = parts {
        for part in parts {
            if let Some(t) = part.get("text").and_then(|v| v.as_str()) {
                if !text.is_empty() {
                    text.push(' ');
                }
                text.push_str(t.trim());
            }
            if let Some(call) = part.get("functionCall") {
                let name = call
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let args = call.get("args").cloned().unwrap_or(Value::Null);
                if !name.is_empty() {
                    calls.push((name, args));
                }
            }
        }
    }
    (text, calls)
}

async fn chat_gemini(
    settings: &store::Settings,
    history: &[Message],
    user_text: &str,
) -> Result<Reply, String> {
    let key = gemini_key()?;
    let model = settings.gemini_model.trim();
    let model = if model.is_empty() {
        "gemini-2.5-flash"
    } else {
        model
    };
    let client = reqwest::Client::new();
    let mut contents = to_contents(history, user_text);
    let system = system_prompt(settings);

    for _round in 0..MAX_TOOL_ROUNDS {
        let body = json!({
            "systemInstruction": { "parts": [{ "text": system }] },
            "contents": contents,
            "tools": [{ "functionDeclarations": tools::declarations() }],
            "generationConfig": { "temperature": 0.4, "maxOutputTokens": 1024 }
        });
        let value = gemini_once(&client, model, &key, &body).await?;
        let (text, calls) = parse_gemini_turn(&value);
        if calls.is_empty() {
            let spoken = if text.trim().is_empty() {
                "I'm not sure how to answer that, sir.".to_string()
            } else {
                text
            };
            return Ok(Reply {
                text: spoken,
                engine: "gemini".to_string(),
                model: model.to_string(),
            });
        }

        // Echo the model's turn (with its function calls) back, then answer
        // each call with a functionResponse so Gemini can finish the job.
        contents.push(value
            .get("candidates")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("content"))
            .cloned()
            .unwrap_or(json!({ "role": "model", "parts": [] })));
        let mut response_parts: Vec<Value> = Vec::new();
        for (name, args) in &calls {
            let result = tools::execute(name, args).await;
            response_parts.push(json!({
                "functionResponse": { "name": name, "response": { "result": result } }
            }));
        }
        contents.push(json!({ "role": "user", "parts": response_parts }));
    }

    Err("I got stuck in a loop of tool calls. Could you rephrase that, sir?".to_string())
}

// ---------------------------------------------------------------------------
// Ollama (optional local engine, chat only)
// ---------------------------------------------------------------------------

async fn chat_ollama(
    settings: &store::Settings,
    history: &[Message],
    user_text: &str,
) -> Result<Reply, String> {
    let host = settings.ollama_host.trim().trim_end_matches('/');
    let host = if host.is_empty() {
        "http://127.0.0.1:11434"
    } else {
        host
    };
    let model = settings.ollama_model.trim();
    let model = if model.is_empty() {
        "llama3.1:8b"
    } else {
        model
    };

    let mut messages: Vec<Value> = vec![json!({
        "role": "system",
        "content": system_prompt(settings)
    })];
    for msg in history {
        let role = if msg.role == "assistant" {
            "assistant"
        } else {
            "user"
        };
        messages.push(json!({ "role": role, "content": msg.text }));
    }
    messages.push(json!({ "role": "user", "content": user_text }));

    let body = json!({ "model": model, "messages": messages, "stream": false });
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{host}/api/chat"))
        .json(&body)
        .send()
        .await
        .map_err(|_| {
            format!(
                "I can't reach Ollama at {host}. Make sure Ollama is running, or switch back to a Gemini key in Settings."
            )
        })?;
    let value: Value = response
        .json()
        .await
        .map_err(|_| "Ollama sent back a reply I couldn't read.".to_string())?;
    let text = value
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if text.is_empty() {
        return Err(format!(
            "Ollama didn't answer. Is the model '{model}' pulled? Run: ollama pull {model}"
        ));
    }
    Ok(Reply {
        text,
        engine: "ollama".to_string(),
        model: model.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contents_map_roles() {
        let history = vec![
            Message {
                role: "user".to_string(),
                text: "hello".to_string(),
            },
            Message {
                role: "assistant".to_string(),
                text: "hi".to_string(),
            },
        ];
        let contents = to_contents(&history, "again");
        assert_eq!(contents.len(), 3);
        assert_eq!(contents[0]["role"], "user");
        assert_eq!(contents[1]["role"], "model");
        assert_eq!(contents[2]["parts"][0]["text"], "again");
    }

    #[test]
    fn parse_turn_reads_text_and_calls() {
        let value = json!({
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [
                        { "text": "Opening it now." },
                        { "functionCall": { "name": "open_app", "args": { "name": "notepad" } } }
                    ]
                }
            }]
        });
        let (text, calls) = parse_gemini_turn(&value);
        assert_eq!(text, "Opening it now.");
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "open_app");
        assert_eq!(calls[0].1["name"], "notepad");
    }

    #[test]
    fn parse_turn_handles_empty() {
        let (text, calls) = parse_gemini_turn(&json!({}));
        assert!(text.is_empty());
        assert!(calls.is_empty());
    }

    #[test]
    fn strips_json_schema_fields_gemini_does_not_support() {
        let mut schema = json!({
            "tools": [{
                "parameters": {
                    "additional_properties": false,
                    "properties": {
                        "nested": { "additionalProperties": false, "type": "OBJECT" }
                    }
                }
            }]
        });
        gemini_compatible_schema(&mut schema);
        assert_eq!(schema, json!({
            "tools": [{ "parameters": { "properties": { "nested": { "type": "OBJECT" } } } }]
        }));
    }

    #[test]
    fn errors_are_readable() {
        let value = json!({ "error": { "message": "API key not valid. Please pass a valid API key." } });
        let msg = gemini_error(&value, 400);
        assert!(msg.contains("rejected the API key"), "got: {msg}");
        let quota = json!({ "error": { "message": "Quota exceeded for quota metric" } });
        assert!(gemini_error(&quota, 429).contains("free Gemini limit"));
    }
}
