---
title: Build the Windows EXE
description: Build the JARVIS Windows installer (OpenJarvis_x64-setup.exe) from source, locally on Windows or on GitHub Actions
---

# Build the Windows EXE

The Windows desktop app is a [Tauri](https://tauri.app/) (Rust + WebView2) shell
around the OpenJarvis web UI, including the [JARVIS HUD](../user-guide/jarvis-mode.md).
The installer is self-contained, so the PC it is installed on needs no
developer tools:

- **Desktop app:** `openjarvis-desktop.exe` (the window, tray icon and HUD).
- **Python backend:** in `<install dir>\src`, with a prebuilt `openjarvis_rust`
  wheel. The app looks there first, so it needs no git clone, Rust or Visual
  Studio Build Tools.
- **`uv.exe`:** installs Python 3.12 and the backend's packages
  (`uv sync --extra jarvis --group desktop-native`) on first launch.
- **`ollama.exe`:** the offline brain, with Ollama's model runtime in
  `<install dir>\lib\ollama` (`llama-server.exe` and its DLLs; without them
  Ollama can download models but not run them). Models run on the CPU, or on
  the GPU through Vulkan, which works with NVIDIA, AMD and Intel graphics
  drivers. Ollama's NVIDIA CUDA libraries (over 1 GB) are left out to keep the
  installer small. If a cloud key is saved in Settings (for example a free
  Gemini key), JARVIS uses that instead.

Because it links Windows system libraries, the installer **must be built on
Windows**. It cannot be cross-compiled from Linux or macOS.

| Path | Best for | Result |
|---|---|---|
| [GitHub Actions](#build-on-github-actions) | No local toolchain; reproducible builds | Artifact `openjarvis-windows-exe`, optionally a pre-release |
| [Local Windows build](#build-locally-on-windows) | Iterating on the app | `OpenJarvis_<version>_x64-setup.exe` |

Both produce:

```text
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/OpenJarvis_<version>_x64-setup.exe
```

## Build on GitHub Actions

The [`Build Windows EXE`](https://github.com/open-jarvis/OpenJarvis/blob/main/.github/workflows/build-windows-exe.yml)
workflow is manual and needs **no repository secrets**. It:

1. builds the `openjarvis_rust` wheel for Python 3.12;
2. stages the backend with `scripts/desktop/stage_jarvis_backend.py`;
3. smoke-tests the staged backend on the Windows runner with
   `scripts/desktop/smoke_jarvis_backend.py`. This installs it exactly as the
   app will, imports the speech, voice and cloud SDKs, calls the PC tools, and
   holds a scripted JARVIS conversation through `jarvis serve` (time zone, a
   saved memory, opening Notepad). The requests come from the app's webview
   origin and must get CORS headers. The stand-in Ollama only has the free
   memory of a busy 8 GB PC, so the server must shrink the model's context, and
   a crashing model must reach the app as a readable error;
4. downloads Ollama and stages its model runtime without the CUDA libraries
   (`frontend/src-tauri/scripts/download-ollama.sh`), then runs a real model
   through it with `scripts/desktop/smoke_ollama_runtime.py`: `ollama.exe` and
   `lib\ollama` laid out as in the install folder, started the way the app
   starts it, answering with `qwen3.5:0.8b`, the model the app picks for 4–8 GB
   PCs;
5. bundles uv and Ollama, builds the NSIS installer, and checks the
   installer's contents (including `lib\ollama\llama-server.exe`, and no CUDA
   libraries).

To run it:

1. The workflow file must exist on the repository's default branch:
   `workflow_dispatch` workflows are only listed once they do. Any branch can
   then be built with `--ref`.
2. In **Settings → Actions → General**, make sure Actions are enabled. Forks
   start with Actions disabled.
3. Run **Actions → Build Windows EXE → Run workflow**. Optionally pass a SemVer
   `version` (the default comes from `frontend/src-tauri/tauri.conf.json`). Tick
   `publish_release` to also attach the installer to a GitHub pre-release,
   which anyone can download without signing in.
4. Download the `openjarvis-windows-exe` artifact from the finished run, or the
   installer from the pre-release.

Pull requests that change the app, the backend or the build run the same
workflow automatically (without publishing), so the installer for a PR is
available as an artifact on its checks page.

From the CLI:

```bash
gh workflow run build-windows-exe.yml --ref main -f publish_release=true
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
  (`rustup target add x86_64-pc-windows-msvc`), and Visual Studio Build Tools
  with the **Desktop development with C++** workload
- [uv](https://docs.astral.sh/uv/)
  (`powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- [Git for Windows](https://git-scm.com/download/win) (recommended: the Ollama
  sidecar script is a Bash script; the PowerShell fallback handles its absence)

These are only needed on the machine that **builds** the installer.

### One command

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
```

or double-click `scripts\build-exe.cmd`.

Useful flags:

```powershell
# Override the installer version
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Version 1.0.2

# Smoke-test the bundled backend and Ollama first (downloads about 2 GB:
# the backend's packages and a small model)
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -SmokeTest

# Also produce an MSI
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Bundles nsis,msi

# Rebuild only the app (deps and sidecar already in place)
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -SkipFrontendInstall -SkipOllama
```

The first build compiles the Rust workspace and typically takes 10–20 minutes;
later builds are incremental.

### What the script does

1. `npm ci` in `frontend/`, and downloads the Ollama sidecar with its model
   runtime (`frontend/src-tauri/binaries/ollama-runtime-x86_64-pc-windows-msvc`,
   without the CUDA libraries).
2. `uv python install 3.12`, then builds the extension wheel:
   `uvx --from "maturin>=1.12.6,<2" maturin build --release --manifest-path rust/crates/openjarvis-python/Cargo.toml --interpreter <python 3.12> --out dist\wheels`.
3. Stages the backend:
   `uv run --no-project --python 3.12 python scripts/desktop/stage_jarvis_backend.py --wheel <wheel> --out frontend/src-tauri/jarvis-backend`.
   This copies the tracked backend files, points `openjarvis-rust` at the
   wheel, pins Python 3.12 and refreshes `uv.lock`.
4. Copies `uv.exe` to `frontend/src-tauri/binaries/uv-x86_64-pc-windows-msvc.exe`.
5. Writes `frontend/src-tauri/tauri.windows-exe.conf.json`:

    ```json
    {
      "version": "1.0.3",
      "bundle": {
        "externalBin": ["binaries/ollama", "binaries/uv"],
        "resources": {
          "jarvis-backend/": "src/",
          "binaries/ollama-runtime-x86_64-pc-windows-msvc/": "./"
        },
        "createUpdaterArtifacts": false
      }
    }
    ```

6. `npx tauri build --target x86_64-pc-windows-msvc --bundles nsis --config src-tauri/tauri.windows-exe.conf.json`,
   with `OPENJARVIS_BACKEND_REPO` set to your `origin` remote. The app clones
   that repository only if it finds no bundled backend.

## What happens on first launch

1. The app asks where its AI should run. The default is the bundled Ollama.
2. It starts Ollama and downloads a local model sized to the PC's memory.
3. It runs `uv sync --extra jarvis --group desktop-native` in
   `<install dir>\src`. uv downloads Python 3.12 and JARVIS's packages
   (speech recognition, the Kokoro voice, the cloud SDKs), so the first launch
   takes a while; later launches start quickly.
4. It starts `jarvis serve --agent jarvis`. Unless a model was pinned, JARVIS
   picks his brain himself: a cloud key saved in **Settings** (Gemini, OpenAI,
   Anthropic, OpenRouter) if there is one, otherwise the local Ollama model.
   Restart the app after saving a key.

Open **JARVIS** in the sidebar for the HUD.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `npm ci` fails with `EBADENGINE` | npm is older than 11.19. Run `npm install --global npm@11.19.0` and reopen the terminal. |
| `error: linker 'link.exe' not found` | Install the **Desktop development with C++** workload (MSVC build tools) required by the Rust MSVC target. Needed only on the build machine. |
| `failed to bundle project: ... binaries/ollama-x86_64-pc-windows-msvc.exe` (or `uv-...`) | A sidecar is missing. Delete `frontend/src-tauri/binaries/*.exe` and rerun without `-SkipOllama`. |
| `uv lock` fails while staging | Python 3.12 is missing or there is no network: run `uv python install 3.12` and retry. |
| App opens to a blank window | WebView2 runtime missing. Install the [Evergreen WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). |
| First launch fails at `uv sync` | No internet, or a proxy blocks PyPI or GitHub. The error shows the command; run it in `<install dir>\src` to see the full output. |
| HUD says the server is not running JARVIS | The app attached to an older `jarvis serve` already running on port 8000. Stop it and relaunch the app. |
| `tauri.windows-exe.conf.json` already exists with stale values | It is generated and git-ignored; delete it and rerun the script. |

## See also

- [JARVIS mode](../user-guide/jarvis-mode.md)
- [Desktop auto-update](../desktop-auto-update.md)
- [Installation](../getting-started/install.md)
- [Contributing](https://github.com/open-jarvis/OpenJarvis/blob/main/CONTRIBUTING.md)
