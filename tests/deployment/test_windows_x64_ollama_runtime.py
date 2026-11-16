"""The x64 Windows installer must ship Ollama's model runtime, minus CUDA.

``ollama.exe`` alone starts the server and downloads models, but every chat
then fails with "llama-server binary not found": the runtime lives in the
archive's ``lib/ollama`` folder. ``download-ollama.sh`` stages that folder for
the installer, leaving out the NVIDIA CUDA libraries that make up most of the
1.5 GB archive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "frontend/src-tauri/scripts"
X64_TARGET = "x86_64-pc-windows-msvc"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("unzip") is None,
    reason="needs bash and unzip",
)


@pytest.fixture
def x64_download(tmp_path):
    """Run the real script on a real ZIP laid out like Ollama's x64 archive."""
    scripts = tmp_path / "src-tauri/scripts"
    scripts.mkdir(parents=True)
    script = scripts / "download-ollama.sh"
    shutil.copy2(SCRIPTS / script.name, script)
    archive = tmp_path / "ollama.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ollama.exe", b"ollama")
        bundle.writestr("lib/ollama/llama-server.exe", b"llama-server")
        bundle.writestr("lib/ollama/ggml-base.dll", b"base")
        bundle.writestr("lib/ollama/ggml-cpu-haswell.dll", b"cpu")
        bundle.writestr("lib/ollama/LLAMA_CPP_LICENSE", b"license")
        bundle.writestr("lib/ollama/vulkan/ggml-vulkan.dll", b"vulkan")
        bundle.writestr("lib/ollama/cuda_v12/ggml-cuda.dll", b"cuda 12")
        bundle.writestr("lib/ollama/cuda_v12/cublasLt64_12.dll", b"cublas 12")
        bundle.writestr("lib/ollama/cuda_v13/ggml-cuda.dll", b"cuda 13")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    curl = stubs / "curl"
    curl.write_text(
        '#!/bin/bash\nfor arg in "$@"; do\n'
        '  case "$arg" in https:*) printf "%s" "$arg" > "$TEST_URL";; esac\n'
        'done\nwhile [ "$1" != "-o" ]; do shift; done\n'
        'cp "$TEST_ARCHIVE" "$2"\n'
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "TEST_ARCHIVE": str(archive),
        "TEST_URL": str(tmp_path / "url"),
    }
    return script, archive, env


def download(script, env, *args):
    return subprocess.run(
        ["bash", str(script), *args], env=env, capture_output=True, text=True
    )


def test_stages_the_runtime_beside_the_sidecar_without_cuda(x64_download):
    script, _, env = x64_download
    result = download(script, env, X64_TARGET)
    assert result.returncode == 0, result.stderr
    assert Path(env["TEST_URL"]).read_text().endswith("/ollama-windows-amd64.zip")
    binaries = script.parent.parent / "binaries"
    assert (binaries / f"ollama-{X64_TARGET}.exe").read_bytes() == b"ollama"

    lib = binaries / f"ollama-runtime-{X64_TARGET}" / "lib" / "ollama"
    assert (lib / "llama-server.exe").read_bytes() == b"llama-server"
    assert (lib / "ggml-base.dll").is_file()
    assert (lib / "ggml-cpu-haswell.dll").is_file()
    assert (lib / "LLAMA_CPP_LICENSE").is_file()
    # Vulkan runs models on any vendor's GPU through the normal driver.
    assert (lib / "vulkan" / "ggml-vulkan.dll").is_file()
    assert not (lib / "cuda_v12").exists()
    assert not (lib / "cuda_v13").exists()
    assert not (lib.parent.parent / "ollama.exe").exists()
    assert "Leaving out lib/ollama/cuda_v12" in result.stdout
    assert "Leaving out lib/ollama/cuda_v13" in result.stdout


def test_rejects_an_archive_without_the_runtime(x64_download):
    script, archive, env = x64_download
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ollama.exe", b"ollama")
    result = download(script, env, X64_TARGET)
    assert result.returncode != 0
    assert "missing its lib/ollama" in result.stdout
    assert not (script.parent.parent / "binaries" / f"ollama-{X64_TARGET}.exe").exists()


def test_a_cached_sidecar_without_the_runtime_is_restaged(x64_download):
    """Builds made before the runtime was bundled left only ollama.exe."""
    script, _, env = x64_download
    binaries = script.parent.parent / "binaries"
    binaries.mkdir()
    (binaries / f"ollama-{X64_TARGET}.exe").write_bytes(b"old sidecar only")

    result = download(script, env, X64_TARGET)

    assert result.returncode == 0, result.stderr
    assert (binaries / f"ollama-{X64_TARGET}.exe").read_bytes() == b"ollama"
    runtime = binaries / f"ollama-runtime-{X64_TARGET}"
    assert (runtime / "lib/ollama/llama-server.exe").is_file()


def test_a_complete_cache_is_kept(x64_download):
    script, _, env = x64_download
    assert download(script, env, X64_TARGET).returncode == 0
    Path(env["TEST_URL"]).unlink()

    result = download(script, env, X64_TARGET)

    assert result.returncode == 0, result.stderr
    assert "Already exists" in result.stdout
    assert not Path(env["TEST_URL"]).exists()  # nothing was downloaded
