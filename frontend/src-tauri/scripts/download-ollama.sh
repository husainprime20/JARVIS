#!/usr/bin/env bash
#
# Download the Ollama binary for the target platform and place it
# in the Tauri binaries/ directory so it can be bundled as an
# externalBin sidecar.
#
# Usage:
#   ./download-ollama.sh                  # auto-detect current platform
#   ./download-ollama.sh aarch64-apple-darwin
#   ./download-ollama.sh universal-apple-darwin
#   ./download-ollama.sh x86_64-unknown-linux-gnu
#   ./download-ollama.sh aarch64-pc-windows-msvc
#
# Ollama distributes platform binaries as archives (.tgz / .tar.zst / .zip).
# This script downloads, extracts the `ollama` CLI binary, renames it
# to the Tauri target-triple convention, and places it under
# frontend/src-tauri/binaries/.
#
# On Windows, ollama.exe alone only starts the server and downloads models.
# Running a model needs the archive's lib/ollama runtime (llama-server.exe and
# its DLLs) beside it, so the script also stages that runtime under
# binaries/ollama-runtime-<target>/ for the installer to place next to
# ollama.exe. For x64 it leaves out the NVIDIA CUDA libraries (over 1 GB);
# models still run on the CPU, and on the GPU through Vulkan.

set -euo pipefail

BINARIES_DIR="$(cd "$(dirname "$0")/../binaries" 2>/dev/null && pwd || echo "$(dirname "$0")/../binaries")"
mkdir -p "$BINARIES_DIR"

# Determine target triple
if [ "${1:-}" != "" ]; then
    TARGET="$1"
else
    ARCH="$(uname -m)"
    OS="$(uname -s)"
    case "$OS" in
        Darwin)
            case "$ARCH" in
                arm64)  TARGET="aarch64-apple-darwin" ;;
                x86_64) TARGET="x86_64-apple-darwin" ;;
                *)      echo "Unsupported arch: $ARCH"; exit 1 ;;
            esac
            ;;
        Linux)
            case "$ARCH" in
                x86_64)  TARGET="x86_64-unknown-linux-gnu" ;;
                aarch64) TARGET="aarch64-unknown-linux-gnu" ;;
                *)       echo "Unsupported arch: $ARCH"; exit 1 ;;
            esac
            ;;
        MINGW*|MSYS*|CYGWIN*|Windows_NT)
            # Git Bash can report x86_64 when running under emulation on ARM.
            case "${PROCESSOR_ARCHITEW6432:-${PROCESSOR_ARCHITECTURE:-$ARCH}}" in
                ARM64|arm64|aarch64) TARGET="aarch64-pc-windows-msvc" ;;
                AMD64|amd64|x86_64) TARGET="x86_64-pc-windows-msvc" ;;
                *) echo "Unsupported Windows arch: $ARCH"; exit 1 ;;
            esac
            ;;
        *)
            echo "Unsupported OS: $OS"; exit 1 ;;
    esac
fi

echo "Target triple: $TARGET"

# Tauri externalBin naming: <name>-<target-triple>[.exe]
SUFFIX=""
case "$TARGET" in
    *windows*) SUFFIX=".exe" ;;
esac
OUT_FILE="$BINARIES_DIR/ollama-${TARGET}${SUFFIX}"
RUNTIME_DIR="$BINARIES_DIR/ollama-runtime-${TARGET}"

# Windows inference also needs the archive's DLLs and lib/ollama runtime;
# without it every request fails with "llama-server binary not found".
NEEDS_RUNTIME=0
case "$TARGET" in
    *windows*) NEEDS_RUNTIME=1 ;;
esac

if [ -f "$OUT_FILE" ] && { [ "$NEEDS_RUNTIME" = 0 ] || [ -d "$RUNTIME_DIR/lib/ollama" ]; }; then
    echo "Already exists: $OUT_FILE"
    echo "Delete it first to re-download."
    exit 0
fi

# Map target triple to Ollama release asset
RELEASE_URL="https://github.com/ollama/ollama/releases/latest/download"

case "$TARGET" in
    *apple-darwin)
        ASSET_URL="${RELEASE_URL}/ollama-darwin.tgz"
        ARCHIVE_TYPE="tgz"
        ;;
    x86_64-unknown-linux-gnu)
        ASSET_URL="${RELEASE_URL}/ollama-linux-amd64.tar.zst"
        ARCHIVE_TYPE="zst"
        ;;
    aarch64-unknown-linux-gnu)
        ASSET_URL="${RELEASE_URL}/ollama-linux-arm64.tar.zst"
        ARCHIVE_TYPE="zst"
        ;;
    x86_64-pc-windows-msvc)
        ASSET_URL="${RELEASE_URL}/ollama-windows-amd64.zip"
        ARCHIVE_TYPE="zip"
        ;;
    aarch64-pc-windows-msvc)
        ASSET_URL="${RELEASE_URL}/ollama-windows-arm64.zip"
        ARCHIVE_TYPE="zip"
        ;;
    *)
        echo "No Ollama binary mapping for target: $TARGET"
        exit 1
        ;;
esac

OLLAMA_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$OLLAMA_TMP_DIR"' EXIT

echo "Downloading: $ASSET_URL"
ARCHIVE_FILE="$OLLAMA_TMP_DIR/ollama-archive"
curl -fSL --progress-bar "$ASSET_URL" -o "$ARCHIVE_FILE"

echo "Extracting..."
case "$ARCHIVE_TYPE" in
    tgz)
        tar xzf "$ARCHIVE_FILE" -C "$OLLAMA_TMP_DIR"
        ;;
    zst)
        if command -v zstd &>/dev/null; then
            zstd -d "$ARCHIVE_FILE" -o "$OLLAMA_TMP_DIR/ollama.tar" --quiet
            tar xf "$OLLAMA_TMP_DIR/ollama.tar" -C "$OLLAMA_TMP_DIR"
        else
            echo "zstd not found. Install with: brew install zstd (macOS) or apt install zstd (Linux)"
            exit 1
        fi
        ;;
    zip)
        unzip -q "$ARCHIVE_FILE" -d "$OLLAMA_TMP_DIR"
        ;;
esac

# Find the ollama binary in the extracted contents
OLLAMA_BIN=""
for candidate in "$OLLAMA_TMP_DIR/bin/ollama" "$OLLAMA_TMP_DIR/ollama" "$OLLAMA_TMP_DIR/ollama.exe"; do
    if [ -f "$candidate" ]; then
        OLLAMA_BIN="$candidate"
        break
    fi
done

if [ -z "$OLLAMA_BIN" ]; then
    echo "Could not find ollama binary in archive. Contents:"
    find "$OLLAMA_TMP_DIR" -type f | head -20
    exit 1
fi

# Tauri's universal macOS bundle needs both architectures in a single sidecar.
if [ "$TARGET" = "universal-apple-darwin" ]; then
    if ! lipo "$OLLAMA_BIN" -verify_arch arm64 x86_64; then
        echo "Ollama's darwin archive does not contain a universal arm64+x86_64 binary"
        exit 1
    fi
fi

if [ "$NEEDS_RUNTIME" = 1 ]; then
    if [ ! -d "$OLLAMA_TMP_DIR/lib/ollama" ]; then
        echo "Windows Ollama archive is missing its lib/ollama inference runtime"
        exit 1
    fi
    if [ "$TARGET" = "x86_64-pc-windows-msvc" ]; then
        # The x64 archive also carries NVIDIA CUDA runtimes (cuda_v12/,
        # cuda_v13/), which make up most of its 1.5 GB. Leave them out:
        # llama-server still runs models on the CPU, and on NVIDIA, AMD and
        # Intel GPUs through the lib/ollama/vulkan backend and the normal
        # graphics driver. ROCm and MLX come in separate archives already.
        for gpu_runtime in "$OLLAMA_TMP_DIR"/lib/ollama/cuda_* \
            "$OLLAMA_TMP_DIR"/lib/ollama/rocm* "$OLLAMA_TMP_DIR"/lib/ollama/mlx*; do
            [ -e "$gpu_runtime" ] || continue
            echo "Leaving out lib/ollama/$(basename "$gpu_runtime") ($(du -sh "$gpu_runtime" | cut -f1))"
            rm -rf "$gpu_runtime"
        done
    fi
    # Preserve the release layout beside the renamed sidecar. The release
    # config maps this directory beside the installed ollama.exe.
    rm -rf "$RUNTIME_DIR"
    mkdir -p "$RUNTIME_DIR"
    for resource in "$OLLAMA_TMP_DIR"/*; do
        case "$(basename "$resource")" in
            ollama.exe|ollama-archive) continue ;;
        esac
        cp -R "$resource" "$RUNTIME_DIR/"
    done
    echo "Inference runtime: $RUNTIME_DIR ($(du -sh "$RUNTIME_DIR" | cut -f1))"
fi

cp "$OLLAMA_BIN" "$OUT_FILE"
chmod +x "$OUT_FILE"

echo "Saved to: $OUT_FILE"
ls -lh "$OUT_FILE"
echo "Done."
