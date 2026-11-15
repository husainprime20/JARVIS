# JARVIS Mode

JARVIS mode turns OpenJarvis into a voice assistant in the style of Iron Man's
JARVIS. Say "Jarvis" and he answers out loud in a British voice. He can open
apps and websites, play YouTube, control volume and media, tell you the time,
weather and system stats, and remember things about you. There is also a HUD
with an arc reactor that shows what he is doing.

It works with any brain: a free Google Gemini key, a paid OpenAI, Anthropic or
OpenRouter key, or a fully offline Ollama model. JARVIS uses the first one he
finds, and you can switch at any time.

!!! note "Built for Windows"
    JARVIS mode is designed and documented for Windows 10 and 11. macOS and
    Linux work too: every command is the same, and the PC tools use `open`
    and `xdg-open` there.

## Quickstart

### 1. Install OpenJarvis from source

Follow [Installation](../getting-started/installation.md) for the
prerequisites: Python 3.10 to 3.13, git, uv, and Rust with the Visual Studio
C++ Build Tools (needed for the native extension). Then, in your OpenJarvis
checkout, install everything JARVIS uses with **one** command:

```powershell
uv sync --extra jarvis --group desktop-native
```

This installs speech recognition, the Kokoro British voice, the Gemini,
OpenAI and Anthropic SDKs, and the time-zone data Windows lacks.

!!! warning "Always use the full command"
    `uv sync` removes anything you did not ask for. Running a plain
    `uv sync`, or `uv sync --extra desktop` on its own, uninstalls JARVIS's
    voice and brain packages. If you use other extras, add them to the
    same line, for example
    `uv sync --extra jarvis --extra tools-search --group desktop-native`.

### 2. Give JARVIS a brain

Choose one, or set up several:

| Brain | Cost | Setup |
| --- | --- | --- |
| Google Gemini | Free tier | Get a key at <https://aistudio.google.com/api-keys>, then `setx GEMINI_API_KEY "your-key"` |
| OpenAI | Paid | `setx OPENAI_API_KEY "sk-..."` |
| Anthropic Claude | Paid | `setx ANTHROPIC_API_KEY "sk-ant-..."` |
| OpenRouter | Paid | `setx OPENROUTER_API_KEY "sk-or-..."` |
| Ollama | Free, offline | Install from <https://ollama.com>, then `ollama pull qwen3.5:4b` (8 GB+ RAM) |

After `setx`, open a **new** terminal so the key is visible.

If you would rather not use environment variables, put the keys in
`%USERPROFILE%\.openjarvis\cloud-keys.env` (one per line, such as
`GEMINI_API_KEY=your-key`). Keys already set in the environment take
priority. Never commit this file or paste keys into chat.

### 3. Create the JARVIS config

```powershell
jarvis init --preset jarvis
```

Add `--force` to replace an existing config. The preset sets JARVIS as the
default agent for `jarvis chat`, `jarvis ask` and the server, keeps the server
on `127.0.0.1`, and selects the `bm_george` voice.

### 4. Check, then talk

```powershell
jarvis listen --check    # shows which brains, microphone and voice are ready
jarvis listen            # voice mode: say "Jarvis, what time is it?"
jarvis listen --text     # type instead of talking
```

## Talking to JARVIS

Start or end a request with his name, such as "Jarvis, open Notepad" or
"What's the weather in Lahore, Jarvis?". Saying just "Jarvis" gets a reply such as
"Yes, sir?", and he then listens for the request. For a few seconds after he
replies, you can follow up without the wake word.

| Say | What happens |
| --- | --- |
| "Jarvis, open Spotify" | Opens the app (built-in list, Start menu, or your `[jarvis.apps]`) |
| "Jarvis, open github.com" | Opens the site in your browser |
| "Jarvis, play lo-fi beats on YouTube" | Finds and plays the video |
| "Jarvis, turn the volume up" / "mute" / "next track" | Media keys and volume |
| "Jarvis, lock my PC" | Locks the screen |
| "Jarvis, how's my battery?" | CPU, memory, disk, battery and uptime |
| "Jarvis, what time is it in Tokyo?" | Time and date in any time zone |
| "Jarvis, what's the weather tomorrow?" | Current weather and forecast |
| "Jarvis, remember that my name is Tony" | Saved to your profile and used from then on |
| "Never mind" | Cancels the current request |
| "Goodbye, Jarvis" | Ends the session |

`jarvis listen` options:

| Option | Effect |
| --- | --- |
| `--brain gemini\|openai\|anthropic\|openrouter\|ollama` | Use a specific brain this session |
| `-m, --model NAME` | Pin an exact model |
| `--text` | Type requests, no microphone needed |
| `--mute` | Show replies without speaking |
| `--no-wake-word` | Treat everything heard as a request |
| `--verbose-heard` | Show speech that did not include the wake word |

## The HUD

The HUD is a full-screen JARVIS page with an arc reactor that changes with
his state (standby, listening, hearing, thinking, speaking), live system
gauges, the active brain and tools, and a transcript. It needs
[Node.js 22](https://nodejs.org) or newer.

```powershell
jarvis gui --hud
```

This starts the JARVIS server and opens the HUD in your browser. If you
already started the server yourself with `jarvis serve`, run
`jarvis gui --hud --no-server` instead. In the desktop app or the normal
browser UI, choose **JARVIS** in the sidebar.

- **Activate** greets you, calibrates to your room noise for a second, then
  listens for "Jarvis". Allow microphone access when the browser asks.
- **Hold to talk** (or hold **Space**) records a request without the wake
  word.
- **Esc** stops him speaking, and the speaker button turns his voice on or
  off.
- You can always type a request instead.

## Memory

When you ask him to remember something, JARVIS writes it to plain Markdown
files in `%USERPROFILE%\.openjarvis\`: `USER.md` for facts about you and
`MEMORY.md` for his own notes. He rereads them on every request. You can open
and edit them in any text editor. Changing `OPENJARVIS_HOME` moves this
folder.

## Configuration

The preset writes a `[jarvis]` section to
`%USERPROFILE%\.openjarvis\config.toml`:

```toml
[jarvis]
brain = "auto"             # or gemini, openai, anthropic, openrouter, ollama
# model = "gemini-3.8-flash"
wake_word = "jarvis"
honorific = "sir"          # how he addresses you; "" for none
require_wake_word = true
follow_up_seconds = 8
greeting = true
mic_threshold = 0          # 0 = calibrate to the room at startup

[jarvis.apps]              # your own apps: "Jarvis, open my editor"
editor = 'C:\Program Files\Microsoft VS Code\Code.exe'
music = "spotify:"
```

With `brain = "auto"`, JARVIS uses `[intelligence] default_model` if it is
set and reachable. Otherwise he tries, in order: Gemini, OpenAI, Anthropic,
OpenRouter, then Ollama (using the best model you have pulled). The default
models are `gemini-3.5-flash-lite` (the largest free daily quota),
`gpt-6-luna`, `claude-haiku-4-5` and `openrouter/auto`.

His voice comes from `[speech]`: `tts_backend = "kokoro"` with `voice_id` set
to `bm_george`, `bm_lewis`, `bf_emma` or `bf_isabella`.

## Privacy and safety

- Speech recognition (faster-whisper) and the Kokoro voice run on your PC.
  With Ollama, nothing leaves your computer.
- A cloud brain receives your requests and his memory files. Google may use
  free-tier Gemini data to improve its products.
- JARVIS can open apps and websites, so the server listens on `127.0.0.1`
  only. Do not expose it to your network.
- If you mention private details such as an email address or phone number in
  a conversation, JARVIS will not send them to web tools (`web_search`,
  `open_website`) in that conversation.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "JARVIS could not find a usable brain" | Follow step 2, open a new terminal, then run `jarvis listen --check` |
| "I've reached my usage limit for the moment" (429) | Free Gemini quotas reset daily at midnight Pacific time. Run `jarvis listen --brain ollama` meanwhile |
| He never hears "Jarvis" | Windows Settings → Privacy & security → Microphone: allow desktop apps. Run with `--verbose-heard` to see what he hears |
| He reacts to background noise | Set `mic_threshold` higher (e.g. `1200`); `0` re-enables calibration |
| No voice | Run the full `uv sync` command from step 1 and restart the server |
| HUD says the server is offline | Start it with `jarvis serve`, or use `jarvis gui --hud` |
| "API port 8000 is already serving an unregistered service" | You already ran `jarvis serve`; use `jarvis gui --hud --no-server` |
