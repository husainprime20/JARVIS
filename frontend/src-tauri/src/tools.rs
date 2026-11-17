//! The tools JARVIS can use: opening apps and websites, time, weather,
//! system stats, a calculator, web search and memory.
//!
//! Each tool has a Gemini function declaration (so the model knows when and
//! how to call it) and a Rust implementation. `execute` runs one tool and
//! returns a short string the model reads back.

use serde_json::{json, Value};
use std::process::Command;

/// Friendly app name -> executable (or shell target) on Windows.
fn app_target(name: &str) -> Option<String> {
    let n = name.trim().to_lowercase();
    let target = match n.as_str() {
        "notepad" | "notes" => "notepad.exe",
        "calculator" | "calc" => "calc.exe",
        "paint" => "mspaint.exe",
        "word" => "winword.exe",
        "excel" => "excel.exe",
        "powerpoint" => "powerpnt.exe",
        "outlook" => "outlook.exe",
        "spotify" => "spotify.exe",
        "discord" => "discord.exe",
        "steam" => "steam.exe",
        "vscode" | "code" | "visual studio code" => "code.exe",
        "chrome" | "google chrome" => "chrome.exe",
        "firefox" => "firefox.exe",
        "edge" | "microsoft edge" => "msedge.exe",
        "file explorer" | "explorer" | "files" => "explorer.exe",
        "terminal" | "cmd" | "command prompt" => "cmd.exe",
        "powershell" => "powershell.exe",
        "task manager" => "taskmgr.exe",
        "settings" => "ms-settings:",
        "camera" => "microsoft.windows.camera:",
        "photos" => "microsoft.windows.photos:",
        "store" | "microsoft store" => "ms-windows-store:",
        "vlc" => "vlc.exe",
        "whatsapp" => "whatsapp.exe",
        "telegram" => "telegram.exe",
        "zoom" => "zoom.exe",
        "teams" | "microsoft teams" => "ms-teams.exe",
        "slack" => "slack.exe",
        "obs" => "obs64.exe",
        "snipping" | "snip" => "snippingtool.exe",
        "clock" => "microsoft.windows.clock:",
        "weather app" => "microsoft.windows.weather:",
        "maps" => "waze.exe",
        _ => return None,
    };
    Some(target.to_string())
}

fn open_target(target: &str) -> Result<(), String> {
    // App targets come only from the static allow-list above. Protocols and
    // web URLs go through Explorer as a process argument (never a shell), so
    // model-generated text cannot inject a second command through cmd.exe.
    let is_protocol = target.contains(':');
    if is_protocol {
        let lower = target.to_ascii_lowercase();
        let allowed = lower.starts_with("https://")
            || lower.starts_with("http://")
            || ["ms-settings:", "microsoft.windows.camera:", "microsoft.windows.photos:", "ms-windows-store:", "microsoft.windows.clock:", "microsoft.windows.weather:"]
                .iter()
                .any(|prefix| lower.starts_with(prefix));
        if !allowed || target.chars().any(|c| c.is_control() || c == '"' || c == '\\') {
            return Err("That link or protocol is not allowed.".to_string());
        }
        Command::new("explorer.exe")
            .arg(target)
            .spawn()
            .map_err(|e| format!("Could not open {target}: {e}"))?;
    } else {
        Command::new(target)
            .spawn()
            .map_err(|e| format!("Could not start {target}: {e}"))?;
    }
    Ok(())
}

/// The function declarations sent to Gemini so it can call the tools.
pub fn declarations() -> Value {
    json!([
        {
            "name": "open_app",
            "description": "Open an application on the user's PC by name (for example Notepad, Spotify, Chrome, Calculator, VS Code).",
            "parameters": {
                "type": "OBJECT",
                "properties": { "name": { "type": "STRING", "description": "The application name." } },
                "required": ["name"]
            }
        },
        {
            "name": "open_website",
            "description": "Open a website in the default browser. Use a full URL starting with https://.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "url": { "type": "STRING", "description": "The full URL to open." } },
                "required": ["url"]
            }
        },
        {
            "name": "play_youtube",
            "description": "Search YouTube and play a video. Use when the user asks to play or watch something on YouTube.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "query": { "type": "STRING", "description": "What to search for on YouTube." } },
                "required": ["query"]
            }
        },
        {
            "name": "current_datetime",
            "description": "Get the current date and time on the user's PC.",
            "parameters": { "type": "OBJECT", "properties": {} }
        },
        {
            "name": "system_stats",
            "description": "Get CPU and memory usage, plus how many cores the PC has.",
            "parameters": { "type": "OBJECT", "properties": {} }
        },
        {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "city": { "type": "STRING", "description": "The city name." } },
                "required": ["city"]
            }
        },
        {
            "name": "web_search",
            "description": "Search the web and return a short summary of the top results.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "query": { "type": "STRING", "description": "What to search for." } },
                "required": ["query"]
            }
        },
        {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression such as '128 * 4 + 12'.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "expression": { "type": "STRING", "description": "The arithmetic expression." } },
                "required": ["expression"]
            }
        },
        {
            "name": "remember",
            "description": "Remember a fact about the user so you can use it later (their name, favourite things, preferences).",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "key": { "type": "STRING", "description": "A short label, e.g. 'user_name' or 'favourite_food'." },
                    "value": { "type": "STRING", "description": "The fact to remember." }
                },
                "required": ["key", "value"]
            }
        },
        {
            "name": "recall",
            "description": "Recall everything JARVIS has remembered about the user.",
            "parameters": { "type": "OBJECT", "properties": {} }
        }
    ])
}

fn arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Run one tool and return a short result string for the model.
pub async fn execute(name: &str, args: &Value) -> String {
    match name {
        "open_app" => {
            let app = arg(args, "name");
            match app_target(&app) {
                Some(target) => match open_target(&target) {
                    Ok(()) => format!("Opened {app}."),
                    Err(e) => format!("I could not open {app}: {e}"),
                },
                None => format!(
                    "I don't know how to open '{app}'. Try Notepad, Calculator, Chrome, Spotify or VS Code."
                ),
            }
        }
        "open_website" => {
            let url = arg(args, "url");
            let url = if url.starts_with("http") {
                url
            } else {
                format!("https://{url}")
            };
            match open_target(&url) {
                Ok(()) => format!("Opened {url}."),
                Err(e) => format!("I could not open the website: {e}"),
            }
        }
        "play_youtube" => {
            let query = arg(args, "query");
            let url = format!(
                "https://www.youtube.com/results?search_query={}",
                urlencode(&query)
            );
            match open_target(&url) {
                Ok(()) => format!("Playing '{query}' on YouTube."),
                Err(e) => format!("I could not open YouTube: {e}"),
            }
        }
        "current_datetime" => {
            let now = chrono::Local::now();
            format!(
                "It is {} on {}.",
                now.format("%I:%M %p"),
                now.format("%A, %d %B %Y")
            )
        }
        "system_stats" => system_stats(),
        "get_weather" => get_weather(&arg(args, "city")).await,
        "web_search" => web_search(&arg(args, "query")).await,
        "calculator" => calculator(&arg(args, "expression")),
        "remember" => {
            let key = arg(args, "key");
            let value = arg(args, "value");
            if key.is_empty() || value.is_empty() {
                return "Nothing to remember.".to_string();
            }
            let mut memory = crate::store::load_memory();
            memory.insert(key.clone(), value.clone());
            match crate::store::save_memory(&memory) {
                Ok(()) => format!("Noted: {key} is {value}."),
                Err(e) => format!("I could not save that memory: {e}"),
            }
        }
        "recall" => {
            let memory = crate::store::load_memory();
            if memory.is_empty() {
                "I don't have anything saved about you yet.".to_string()
            } else {
                let mut parts: Vec<String> = memory
                    .iter()
                    .map(|(k, v)| format!("{k}: {v}"))
                    .collect();
                parts.sort();
                format!("Here's what I remember about you: {}.", parts.join("; "))
            }
        }
        _ => format!("Unknown tool: {name}"),
    }
}

fn system_stats() -> String {
    // Use Windows' built-in CIM provider instead of bundling a native system
    // stats library. This keeps the desktop binary lean and dependency-light.
    let script = "$os=Get-CimInstance Win32_OperatingSystem; $cpu=Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average; $cores=(Get-CimInstance Win32_Processor | Measure-Object -Sum NumberOfLogicalProcessors).Sum; $used=[math]::Round(($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/1024); $total=[math]::Round($os.TotalVisibleMemorySize/1024); Write-Output \"$([math]::Round($cpu.Average))|$used|$total|$cores\"";
    let output = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .output();
    let Ok(output) = output else {
        return format!("I couldn't read system stats. Your PC has {} logical processors.", std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1));
    };
    let raw = String::from_utf8_lossy(&output.stdout);
    let fields: Vec<&str> = raw.trim().split('|').collect();
    if fields.len() != 4 {
        return "I couldn't read system stats from Windows.".to_string();
    }
    let (cpu, used_mb, total_mb, cores) = (fields[0], fields[1], fields[2], fields[3]);
    let used: f64 = used_mb.parse().unwrap_or(0.0);
    let total: f64 = total_mb.parse().unwrap_or(0.0);
    let pct = if total > 0.0 { (used / total * 100.0).round() } else { 0.0 };
    format!("Your PC has {cores} logical cores. CPU is at {cpu}%. Memory: {used_mb} of {total_mb} MB used ({pct}%).")
}

async fn get_weather(city: &str) -> String {
    if city.is_empty() {
        return "Which city should I check?".to_string();
    }
    let client = match reqwest::Client::new().get("https://geocoding-api.open-meteo.com/v1/search").query(&[("name", city), ("count", "1")]).send().await {
        Ok(r) => r,
        Err(_) => return "I couldn't reach the weather service.".to_string(),
    };
    let geo: Value = match client.json().await {
        Ok(v) => v,
        Err(_) => return "I couldn't read the weather service reply.".to_string(),
    };
    let place = geo
        .get("results")
        .and_then(|r| r.get(0))
        .cloned()
        .unwrap_or(Value::Null);
    let (lat, lon, label) = (
        place.get("latitude").and_then(|v| v.as_f64()),
        place.get("longitude").and_then(|v| v.as_f64()),
        place
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(city)
            .to_string(),
    );
    let (Some(lat), Some(lon)) = (lat, lon) else {
        return format!("I couldn't find a place called {city}.");
    };
    let forecast_response = match reqwest::Client::new()
        .get("https://api.open-meteo.com/v1/forecast")
        .query(&[
            ("latitude", &lat.to_string()),
            ("longitude", &lon.to_string()),
            ("current_weather", "true"),
        ])
        .send()
        .await
    {
        Ok(response) => response,
        Err(_) => return "I couldn't reach the weather service.".to_string(),
    };
    let forecast: Value = match forecast_response.json().await {
        Ok(value) => value,
        Err(_) => return "I couldn't read the weather service reply.".to_string(),
    };
    let current = forecast.get("current_weather").cloned().unwrap_or(Value::Null);
    let temp = current.get("temperature").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let code = current.get("weathercode").and_then(|v| v.as_i64()).unwrap_or(-1);
    let wind = current.get("windspeed").and_then(|v| v.as_f64()).unwrap_or(0.0);
    format!(
        "In {label} it is {}°C with a wind of {} km/h. {}",
        temp.round(),
        wind.round(),
        weather_text(code)
    )
}

fn weather_text(code: i64) -> String {
    match code {
        0 => "Clear sky.".to_string(),
        1 | 2 | 3 => "Partly cloudy.".to_string(),
        45 | 48 => "Foggy.".to_string(),
        51 | 53 | 55 | 61 | 63 | 65 | 80 | 81 | 82 => "It's raining.".to_string(),
        71 | 73 | 75 | 85 | 86 => "It's snowing.".to_string(),
        95 | 96 | 99 => "There are thunderstorms.".to_string(),
        _ => String::new(),
    }
}

async fn web_search(query: &str) -> String {
    if query.is_empty() {
        return "What should I search for?".to_string();
    }
    // DuckDuckGo instant answers (free, no key). Falls back to opening the
    // search in the browser when there's no instant answer.
    let response = match reqwest::Client::new()
        .get("https://api.duckduckgo.com/")
        .query(&[("q", query), ("format", "json"), ("no_html", "1")])
        .send()
        .await
    {
        Ok(response) => response,
        Err(_) => {
            let _ = open_target(&format!("https://duckduckgo.com/?q={}", urlencode(query)));
            return format!("I opened a web search for '{query}'.");
        }
    };
    let value: Value = match response.json().await {
        Ok(value) => value,
        Err(_) => {
            let _ = open_target(&format!("https://duckduckgo.com/?q={}", urlencode(query)));
            return format!("I opened a web search for '{query}'.");
        }
    };
    let abstract_text = value
        .get("AbstractText")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if !abstract_text.is_empty() {
        return abstract_text.to_string();
    }
    let first = value
        .get("RelatedTopics")
        .and_then(|v| v.as_array())
        .and_then(|a| a.first())
        .and_then(|t| t.get("Text"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if !first.is_empty() {
        return first.to_string();
    }
    let _ = open_target(&format!("https://duckduckgo.com/?q={}", urlencode(query)));
    format!("I opened a web search for '{query}'.")
}

/// Minimal percent-encoding for a query string value.
pub fn urlencode(input: &str) -> String {
    let mut out = String::new();
    for byte in input.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

// ---------------------------------------------------------------------------
// A tiny, safe arithmetic evaluator (no eval, no external crate).
// ---------------------------------------------------------------------------

fn calculator(expression: &str) -> String {
    let expr = expression.trim();
    if expr.is_empty() {
        return "What should I calculate?".to_string();
    }
    match eval(expr) {
        Ok(value) => {
            let shown = if value.fract() == 0.0 && value.is_finite() {
                format!("{}", value as i64)
            } else {
                format!("{value:.6}")
                    .trim_end_matches('0')
                    .trim_end_matches('.')
                    .to_string()
            };
            format!("{expr} = {shown}.")
        }
        Err(_) => format!("I couldn't work out '{expr}'."),
    }
}

struct Parser<'a> {
    chars: std::iter::Peekable<std::str::Chars<'a>>,
}

impl<'a> Parser<'a> {
    fn peek(&mut self) -> Option<char> {
        self.chars.peek().copied()
    }
    fn next(&mut self) -> Option<char> {
        self.chars.next()
    }
    fn skip_ws(&mut self) {
        while matches!(self.peek(), Some(c) if c.is_whitespace()) {
            self.next();
        }
    }
    fn parse_expr(&mut self) -> Result<f64, ()> {
        let mut value = self.parse_term()?;
        loop {
            self.skip_ws();
            match self.peek() {
                Some('+') => {
                    self.next();
                    value += self.parse_term()?;
                }
                Some('-') => {
                    self.next();
                    value -= self.parse_term()?;
                }
                _ => return Ok(value),
            }
        }
    }
    fn parse_term(&mut self) -> Result<f64, ()> {
        let mut value = self.parse_factor()?;
        loop {
            self.skip_ws();
            match self.peek() {
                Some('*') => {
                    self.next();
                    value *= self.parse_factor()?;
                }
                Some('/') => {
                    self.next();
                    let divisor = self.parse_factor()?;
                    if divisor == 0.0 {
                        return Err(());
                    }
                    value /= divisor;
                }
                Some('%') => {
                    self.next();
                    let divisor = self.parse_factor()?;
                    if divisor == 0.0 {
                        return Err(());
                    }
                    value %= divisor;
                }
                _ => return Ok(value),
            }
        }
    }
    fn parse_factor(&mut self) -> Result<f64, ()> {
        self.skip_ws();
        match self.peek() {
            Some('(') => {
                self.next();
                let value = self.parse_expr()?;
                self.skip_ws();
                if self.next() != Some(')') {
                    return Err(());
                }
                Ok(value)
            }
            Some('-') => {
                self.next();
                Ok(-self.parse_factor()?)
            }
            Some('+') => {
                self.next();
                self.parse_factor()
            }
            _ => self.parse_number(),
        }
    }
    fn parse_number(&mut self) -> Result<f64, ()> {
        let mut text = String::new();
        let mut seen_digit = false;
        while let Some(c) = self.peek() {
            if c.is_ascii_digit() {
                seen_digit = true;
                text.push(c);
                self.next();
            } else if c == '.' {
                text.push(c);
                self.next();
            } else {
                break;
            }
        }
        if !seen_digit {
            return Err(());
        }
        text.parse::<f64>().map_err(|_| ())
    }
}

fn eval(expression: &str) -> Result<f64, ()> {
    let mut parser = Parser {
        chars: expression.chars().peekable(),
    };
    let value = parser.parse_expr()?;
    parser.skip_ws();
    if parser.peek().is_some() {
        return Err(());
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calculator_handles_precedence_and_brackets() {
        assert_eq!(eval("2 + 3 * 4"), Ok(14.0));
        assert_eq!(eval("(2 + 3) * 4"), Ok(20.0));
        assert_eq!(eval("100 / 4 - 5"), Ok(20.0));
        assert_eq!(eval("-3 + 10"), Ok(7.0));
    }

    #[test]
    fn calculator_rejects_bad_input() {
        assert!(eval("2 +").is_err());
        assert!(eval("hello").is_err());
        assert!(eval("1 / 0").is_err());
        assert!(eval("").is_err());
    }

    #[test]
    fn urlencode_escapes_spaces_and_symbols() {
        assert_eq!(urlencode("a b&c"), "a+b%26c");
        assert_eq!(urlencode("plain-1"), "plain-1");
    }

    #[test]
    fn known_apps_resolve() {
        assert_eq!(app_target("Notepad").as_deref(), Some("notepad.exe"));
        assert_eq!(app_target("spotify").as_deref(), Some("spotify.exe"));
        assert_eq!(app_target("something unknown"), None);
    }
}
