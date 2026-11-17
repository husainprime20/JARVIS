"""Checks for scripts/desktop/smoke_ollama_runtime.py.

The Windows workflow runs the real script against the real bundled Ollama.
Here a fake ``ollama`` stands in. Like the real one, it can only answer a chat
when ``lib/ollama/llama-server`` sits beside it, so the end-to-end test fails
if the script does not lay the runtime out the way the installer does.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts/desktop/smoke_ollama_runtime.py"
    spec = importlib.util.spec_from_file_location("smoke_ollama_runtime", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()

FAKE_OLLAMA = textwrap.dedent(
    """
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    APP_DIR = Path(sys.argv[1])
    RUNTIME_OK = (APP_DIR / "lib" / "ollama" / "llama-server").is_file()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, lines=None):
            data = (
                b"".join(json.dumps(line).encode() + b"\\n" for line in lines)
                if lines is not None
                else json.dumps(body).encode()
            )
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/version":
                self.send(200, {"version": "0.0.0-fake"})
            elif self.path == "/api/ps":
                self.send(200, {"models": [{"name": "m", "size": 10, "size_vram": 0}]})
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/pull":
                self.send(200, None, lines=[
                    {"status": "pulling manifest"},
                    {"status": "pulling 1234", "total": 100, "completed": 50},
                    {"status": "pulling 1234", "total": 100, "completed": 100},
                    {"status": "success"},
                ])
            elif self.path == "/api/chat":
                if not RUNTIME_OK:
                    self.send(500, {"error": "error starting llama-server: "
                                    "llama-server binary not found"})
                    return
                assert body["options"]["num_ctx"] == 4096
                self.send(200, {"message": {"role": "assistant", "content": "Ready."},
                                "eval_count": 3, "eval_duration": 100000000})
            else:
                self.send(404, {"error": "not found"})

    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    assert sys.argv[2] == "serve", sys.argv
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
    """
)


@pytest.fixture
def staged(tmp_path):
    """A sidecar and a runtime folder, as download-ollama.sh stages them."""
    if sys.platform == "win32":
        pytest.skip("the fake sidecar is a shell script")
    server = tmp_path / "fake_ollama.py"
    server.write_text(FAKE_OLLAMA)
    sidecar = tmp_path / "binaries" / "ollama-x86_64-unknown-linux-gnu"
    sidecar.parent.mkdir()
    sidecar.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{server}" "$(dirname "$0")" "$@"\n'
    )
    sidecar.chmod(0o755)
    runtime = tmp_path / "binaries" / "runtime"
    lib = runtime / "lib" / "ollama"
    (lib / "vulkan").mkdir(parents=True)
    llama_server = lib / "llama-server"
    llama_server.write_text("#!/bin/sh\necho 'version: 1 (fake)'\n")
    llama_server.chmod(0o755)
    (lib / "ggml-base.so").write_bytes(b"x" * 1000)
    (lib / "vulkan" / "ggml-vulkan.so").write_bytes(b"x" * 3000)
    return sidecar, runtime


def test_runs_a_model_through_the_assembled_install_layout(staged, capsys):
    sidecar, runtime = staged

    code = smoke.main(["--ollama", str(sidecar), "--runtime", str(runtime)])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "GPU backends: vulkan" in out
    assert "version: 1 (fake)" in out
    assert "pulled qwen3.5:0.8b" in out
    assert "answered 'Ready.'" in out
    assert "runs on CPU" in out
    assert "Bundled Ollama smoke test passed." in out


def test_a_runtime_without_llama_server_fails(staged, capsys):
    sidecar, runtime = staged
    (runtime / "lib" / "ollama" / "llama-server").unlink()

    code = smoke.main(["--ollama", str(sidecar), "--runtime", str(runtime)])

    assert code == 1
    assert "llama-server is missing" in capsys.readouterr().out


def test_an_oversized_runtime_fails_and_names_the_backends(staged, capsys):
    sidecar, runtime = staged
    cuda = runtime / "lib" / "ollama" / "cuda_v13"
    cuda.mkdir()
    (cuda / "ggml-cuda.so").write_bytes(b"x" * 3 * 1_048_576)

    code = smoke.main(
        [
            "--ollama",
            str(sidecar),
            "--runtime",
            str(runtime),
            "--max-runtime-mb",
            "2",
            "--skip-model",
        ]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "over the 2 MB budget" in out
    assert "cuda_v13 3 MB" in out


def test_the_installer_layout_puts_the_runtime_beside_ollama(staged, tmp_path):
    sidecar, runtime = staged
    app = tmp_path / "OpenJarvis"

    exe = smoke.assemble_install_layout(sidecar, runtime, app)

    assert exe == app / "ollama"
    assert (app / "lib" / "ollama" / "llama-server").is_file()
    assert (app / "lib" / "ollama" / "vulkan" / "ggml-vulkan.so").is_file()


def test_windows_runtime_names_the_exe():
    assert smoke.llama_server_name("win32") == "llama-server.exe"
    assert smoke.llama_server_name("linux") == "llama-server"
