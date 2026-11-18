---
title: Build the Windows EXE
description: Build the JARVIS Rust desktop installer on Windows or GitHub Actions
---

# Build the Windows EXE

JARVIS Desktop is a native Rust/Tauri application with a React WebView UI. Its
assistant core, storage, Gemini client, tools, tray, hotkey and overlay are
implemented in Rust. The installer does **not** bundle Python, install a model,
or include an Ollama executable. Gemini is the default brain; users may opt in
to an Ollama endpoint from Settings.

The Windows installer must be built on Windows because it links Windows system
libraries; cross-compiling from Linux or macOS is not supported.

## Local Windows build

Requirements: Windows 10/11 x64, Node.js 22.22+, npm 11.19.x, Rust with the
`x86_64-pc-windows-msvc` target, and Visual Studio Build Tools with the C++
workload. WebView2 is required at runtime and ships with current Windows 10/11.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
```

To skip `npm ci` when dependencies are already installed:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -SkipFrontendInstall
```

The NSIS installer is produced under:

```text
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/
```

It contains the Rust/Tauri app and frontend. No Python, uv, Ollama sidecar,
model download, or Python backend smoke test is part of the build.

## GitHub Actions

The [`Build Windows EXE`](https://github.com/husainprime20/JARVIS/blob/main/.github/workflows/build-windows-exe.yml)
workflow runs on relevant pull requests and can be run manually. It installs
Node/npm and Rust, builds the unsigned NSIS installer, and uploads the installer
as the `jarvis-windows-exe` artifact. A manual run may optionally publish an
unsigned GitHub pre-release. Windows SmartScreen may require **More info → Run
anyway** because the installer is not code-signed.

## First run

Launch JARVIS, open Settings, and save a Gemini API key from
<https://aistudio.google.com/api-keys>. The key is stored in Windows Credential
Manager. JARVIS uses the `gemini-2.5-flash` model by default. Ollama is an
optional setting and is neither installed nor started by the app.

`Ctrl+Alt+J` brings the assistant forward. The floating arc-reactor overlay is
always on top. Voice uses Web Speech when available; the Windows WebView fallback
records a short clip and uses Gemini for transcription.
