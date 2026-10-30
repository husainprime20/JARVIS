---
title: Build the Windows EXE
description: Build OpenJarvis_x64-setup.exe from source — locally on Windows or on GitHub Actions
---

# Build the Windows EXE

The Windows desktop app is a [Tauri](https://tauri.app/) (Rust + WebView2) shell
around the OpenJarvis web UI, with Ollama bundled as a sidecar. Because it links
Windows system libraries, the installer **must be built on Windows** — it cannot
be cross-compiled from Linux or macOS.

Two supported paths produce the same unsigned NSIS installer:

| Path | Best for | Result |
|---|---|---|
| [GitHub Actions](#build-on-github-actions) | No local toolchain; reproducible builds | Artifact `openjarvis-windows-exe` |
| [Local Windows build](#build-locally-on-windows) | Iterating on the app | `OpenJarvis_<version>_x64-setup.exe` |

Both produce:

```text
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/OpenJarvis_<version>_x64-setup.exe
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/openjarvis-desktop.exe   # portable
```

## Build on GitHub Actions

The [`Build Windows EXE`](https://github.com/open-jarvis/OpenJarvis/blob/main/.github/workflows/build-windows-exe.yml)
workflow is manual, needs **no repository secrets**, and never publishes a
release.

1. Push the workflow file to the repository's default branch. `workflow_dispatch`
   workflows are only listed once they exist on the default branch.
2. In **Settings → Actions → General**, make sure Actions are enabled. Forks
   start with Actions disabled.
3. Run **Actions → Build Windows EXE → Run workflow**. Optionally pass a SemVer
   `version`; the default comes from `frontend/src-tauri/tauri.conf.json`.
4. Download the `openjarvis-windows-exe` artifact from the finished run.

From the CLI:

```bash
gh workflow run build-windows-exe.yml --ref main
gh run watch
gh run download --name openjarvis-windows-exe
```

!!! note "Unsigned build"
    The installer is not code-signed, so Windows SmartScreen may warn on first
    launch ("More info" → "Run anyway"). Signing requires a code-signing
    certificate; the upstream release workflow handles that separately in
    [`desktop.yml`](https://github.com/open-jarvis/OpenJarvis/blob/main/.github/workflows/desktop.yml).

## Build locally on Windows

### Prerequisites

- Windows 10/11 x64
- [Node.js](https://nodejs.org/) **22.22+** with **npm 11.19+**
  (`npm install --global npm@11.19.0`)
- [Rust](https://rustup.rs/) **1.89+** with the `x86_64-pc-windows-msvc` target
  (`rustup target add x86_64-pc-windows-msvc`)
- [Git for Windows](https://git-scm.com/download/win) (recommended — the Ollama
  sidecar script is a Bash script; the PowerShell fallback handles its absence)

### One command

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
```

or double-click `scripts\build-exe.cmd`.

Useful flags:

```powershell
# Override the installer version
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Version 1.0.2

# Also produce an MSI
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Bundles nsis,msi

# Rebuild only the app (deps and sidecar already in place)
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -SkipFrontendInstall -SkipOllama
```

The first build compiles the Rust workspace and typically takes 10–20 minutes;
later builds are incremental.

### Manual equivalent

```powershell
cd frontend
npm ci
cd src-tauri\scripts
bash download-ollama.sh x86_64-pc-windows-msvc
cd ..\..
@'
{
  "version": "1.0.1",
  "bundle": {
    "externalBin": ["binaries/ollama"],
    "createUpdaterArtifacts": false
  }
}
'@ | Set-Content -Encoding utf8 src-tauri\tauri.windows-exe.conf.json
npx tauri build --target x86_64-pc-windows-msvc --bundles nsis `
  --config src-tauri/tauri.windows-exe.conf.json
```

## What the installer contains

- `openjarvis-desktop.exe` — the Tauri window (WebView2).
- `ollama.exe` — bundled sidecar; the model runtime and weights download on
  first use, which keeps the installer small.

The desktop app is a **client**: it expects the OpenJarvis backend on
`http://localhost:8000`. Start it before launching the app:

```bash
jarvis serve          # or: ./scripts/quickstart.sh
```

On native Windows, follow
[Windows-native setup](../getting-started/windows-native.md) to register the
scheduled-task service that keeps the backend running.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `npm ci` fails with `EBADENGINE` | npm is older than 11.19. Run `npm install --global npm@11.19.0` and reopen the terminal. |
| `error: linker 'link.exe' not found` | Install the **Desktop development with C++** workload (MSVC build tools) required by the Rust MSVC target. |
| `failed to bundle project: ... binaries/ollama-x86_64-pc-windows-msvc.exe` | The sidecar download did not run or failed. Delete `frontend/src-tauri/binaries/ollama-*.exe` and rerun without `-SkipOllama`. |
| App opens to a blank window | WebView2 runtime missing — install the [Evergreen WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). |
| App shows "backend unreachable" | Start the backend (`jarvis serve`); the app does not bundle Python. |
| `tauri.windows-exe.conf.json` already exists with stale values | It is generated and git-ignored; delete it and rerun the script. |

## See also

- [Desktop auto-update](../desktop-auto-update.md)
- [Installation](../getting-started/install.md)
- [Contributing](https://github.com/open-jarvis/OpenJarvis/blob/main/CONTRIBUTING.md)
