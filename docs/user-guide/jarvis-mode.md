# JARVIS Desktop

JARVIS is your Rust-powered Windows assistant: a redesigned arc-reactor HUD,
voice input and spoken replies, useful PC tools, a floating always-on-top orb,
and a global hotkey. Press **Ctrl+Alt+J** from any app to bring JARVIS forward.

## First launch

1. Install and open the JARVIS Windows app.
2. Open **Settings** → **API keys** and paste a free Gemini key from
   <https://aistudio.google.com/api-keys>.
3. Keep **Cloud API key** selected (the default). JARVIS uses Gemini Flash Lite
   and does not download or start a local model. Your key is stored in Windows
   Credential Manager and is only sent to Google's Gemini API when you chat.
4. Press the microphone button, allow microphone access, say a request, then
   press it again to finish if the WebView uses its recording fallback. You can
   also type in the message box.

## Voice

When Microsoft WebView2 exposes its Speech Recognition API, JARVIS listens for
“Jarvis” and then transcribes your request. If that API is missing, the
microphone button records a short push-to-talk clip and sends it to Gemini for
transcription. Check Windows **Settings → Privacy & security → Microphone** if
JARVIS cannot access the microphone. Replies are spoken using an available
English system voice (a British voice is preferred when installed).

You can turn off the wake word or spoken replies from JARVIS Settings. Voice
transcription in the recording fallback needs a Gemini API key; the local
Ollama chat option does not provide local speech recognition.

## What JARVIS can do

Ask it to open installed Windows apps (resolved from Start Menu shortcuts), open a website, search YouTube or the web, calculate arithmetic, check weather, report the time and PC stats, control volume/media playback, lock the screen, or remember/recall a fact. JARVIS uses model function-calling to choose tools and reports errors rather than pretending an action succeeded. The most recent 20 chat turns and saved memory/settings are stored locally in the app-data folder; API keys stay in Windows Credential Manager.

## Choose a brain

- **Cloud API key (Gemini)** is the default: light on the PC, fast, and requires
  an internet connection. Save a key in Settings.
- **Local Ollama** is optional: install and run Ollama yourself, then select it
  in Settings and enter its address and model. JARVIS does not install Ollama
  or download models for you.

## Troubleshooting

- **“No Gemini API key is saved”**: open Settings and save a valid key.
- **Key rejected / quota message**: check the key and Google AI Studio quota.
- **Microphone blocked**: enable microphone access for desktop apps in Windows
  privacy settings, then restart voice listening.
- **Ollama unreachable**: confirm Ollama is running and the host/model in
  Settings match your installation; or switch back to Gemini.
- **Hotkey does not open JARVIS**: try **Ctrl+Alt+J** and check that another app
  or utility has not claimed the same shortcut.
