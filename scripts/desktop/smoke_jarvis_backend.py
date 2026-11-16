"""End-to-end check of a staged JARVIS backend, run the way the desktop app runs it.

The Windows EXE workflow runs this on the build machine before packaging:

1. copy the staged backend (``stage_jarvis_backend.py``) to a temporary folder;
2. run the app's exact install command, ``uv sync --extra jarvis --group
   desktop-native``, which must install the prebuilt ``openjarvis_rust`` wheel;
3. import the native extension, speech, voice and cloud SDKs, run
   ``jarvis listen --check`` and call the PC tools directly;
4. start ``jarvis serve --agent jarvis`` against a scripted stand-in Ollama,
   speak a sentence in the British voice and transcribe it back (the HUD's
   speech path), and hold a conversation that makes JARVIS use his tools: a
   time zone, saving and recalling a memory, and (on Windows) opening Notepad.
   Requests come from the app's webview origin and must get CORS headers. The
   stand-in has the free memory of a busy 8 GB PC, so the server must shrink
   the model's context, and a crashing model must reach the app as a readable
   error rather than "Failed to fetch";
5. start it again the way the app's cloud source does (``--engine cloud
   --brain gemini`` with the saved key) while Ollama is off, against a scripted
   stand-in for Google's Gemini API: JARVIS must answer through Gemini, use a
   tool, send the key, and report a rejected key to the app readably.

Usage::

    python scripts/desktop/smoke_jarvis_backend.py \\
        --backend frontend/src-tauri/jarvis-backend

For a quick local run against an existing environment (no copy or sync)::

    python scripts/desktop/smoke_jarvis_backend.py --project . \\
        --python .venv/bin/python --skip-imports

Failures are also printed as GitHub Actions error annotations.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
SYNC_COMMAND = ["sync", "--extra", "jarvis", "--group", "desktop-native"]
CLOUD_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)
STAND_IN_MODEL = "qwen3.5:4b"
# The stand-in behaves like Ollama on a busy 8 GB PC: it cannot load the model
# with JARVIS's default 16k context, only with this much or less. The server
# must shrink the context instead of failing every request.
STAND_IN_FITTING_CTX = 8192
LOW_MEMORY_ERROR = (
    "model requires more system memory (3.1 GiB) than is available (2.4 GiB)"
)
# A request containing this makes the stand-in's runner crash, to check that a
# failing brain reaches the HUD as a readable error.
CRASH_PHRASE = "simulate a crash"
CRASH_ERROR = "llama runner process has terminated: exit status 2"
# The desktop app's webview origin; the server must answer it with CORS headers.
APP_ORIGIN = "http://tauri.localhost"
# The cloud source: what the app runs when the user picks a Gemini key at
# setup. Ollama stays off, so JARVIS must answer through Gemini alone.
CLOUD_SOURCE_ARGS = ("--engine", "cloud", "--brain", "gemini", "--agent", "jarvis")
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TEST_KEY = "smoke-test-gemini-key"
GEMINI_KEY_ERROR = "API key not valid. Please pass a valid API key."
# A request containing this is refused like a mistyped key, to check that the
# HUD hears about it instead of "Failed to fetch".
REJECT_PHRASE = "simulate a rejected key"
IMPORTS = (
    "openjarvis_rust",
    "faster_whisper",
    "sounddevice",
    "soundfile",
    "kokoro",
    "google.genai",
    "openai",
    "anthropic",
)
DENIAL_MARKERS = ("denied for", "security block", "taint")


class SmokeFailure(AssertionError):
    """A check failed; the message says what the user would have hit."""


def _escape_annotation(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def report_failure(title: str, message: str) -> None:
    print(f"FAIL {title}: {message}", flush=True)
    if IN_ACTIONS:
        print(
            f"::error title={title}::{_escape_annotation(message)[:6000]}", flush=True
        )


def report_warning(title: str, message: str) -> None:
    print(f"WARN {title}: {message}", flush=True)
    if IN_ACTIONS:
        print(
            f"::warning title={title}::{_escape_annotation(message)[:3000]}", flush=True
        )


def tail(text: str, limit: int = 2500) -> str:
    return text[-limit:] if len(text) > limit else text


# ---------------------------------------------------------------------------
# Stand-in Ollama: scripted tool calls instead of a real model
# ---------------------------------------------------------------------------


def scripted_reply(body: dict[str, Any]) -> dict[str, Any]:
    """What a tool-calling model would answer, for the conversation below."""
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        content = str(last.get("content", ""))
        try:
            summary = json.loads(content).get("summary") or content
        except (ValueError, AttributeError):
            summary = content
        return {"role": "assistant", "content": f"Certainly, sir. {summary[:400]}"}

    text = str(last.get("content", "")).lower()
    tools = {t.get("function", {}).get("name") for t in body.get("tools") or []}

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }

    if "time" in text and "current_datetime" in tools:
        return call("current_datetime", {"timezone": "Asia/Karachi"})
    if "remember" in text and "user_profile_manage" in tools:
        return call("user_profile_manage", {"action": "add", "entry": "Name: Nova"})
    if "open notepad" in text and "open_app" in tools:
        return call("open_app", {"name": "notepad"})
    system = messages[0].get("content", "") if messages else ""
    if "name" in text:
        known = "Name: Nova" in str(system)
        answer = "Your name is Nova, sir." if known else "I don't know it yet, sir."
        return {"role": "assistant", "content": answer}
    return {"role": "assistant", "content": "Good evening, sir."}


def stand_in_failure(body: dict[str, Any]) -> str | None:
    """The error the stand-in Ollama answers *body* with, if any."""
    messages = body.get("messages") or []
    users = [m for m in messages if m.get("role") == "user"]
    if users and CRASH_PHRASE in str(users[-1].get("content", "")).lower():
        return CRASH_ERROR
    num_ctx = (body.get("options") or {}).get("num_ctx") or 4096
    if num_ctx > STAND_IN_FITTING_CTX:
        return LOW_MEMORY_ERROR
    return None


class _StandInOllama(BaseHTTPRequestHandler):
    # Every /api/chat request as (num_ctx, error or None), for the checks.
    chats: list[tuple[int | None, str | None]] = []

    def log_message(self, *args: Any) -> None:  # keep the CI log readable
        pass

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path.startswith("/api/tags"):
            self._send(
                {
                    "models": [
                        {"name": "nomic-embed-text:latest"},
                        {"name": STAND_IN_MODEL},
                    ]
                }
            )
        elif self.path.startswith("/api/version"):
            self._send({"version": "0.12.0"})
        else:
            self._send({})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/api/chat"):
            error = stand_in_failure(body)
            num_ctx = (body.get("options") or {}).get("num_ctx")
            _StandInOllama.chats.append((num_ctx, error))
            if error:
                self._send({"error": error}, status=500)
                return
            self._send(
                {
                    "model": body.get("model"),
                    "message": scripted_reply(body),
                    "done": True,
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            )
        else:
            self._send({})


def start_stand_in_ollama() -> tuple[ThreadingHTTPServer, int]:
    _StandInOllama.chats = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StandInOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _gemini_answer(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    }


def gemini_reply(body: dict[str, Any]) -> dict[str, Any]:
    """What Gemini would answer, for the cloud-brain conversation below."""
    contents = body.get("contents") or []
    parts = (contents[-1].get("parts") if contents else None) or []
    for part in parts:
        response = part.get("functionResponse") or part.get("function_response")
        if response:
            result = str((response.get("response") or {}).get("result", ""))
            try:
                summary = json.loads(result).get("summary") or result
            except (ValueError, AttributeError):
                summary = result
            return _gemini_answer([{"text": f"Certainly, sir. {summary[:400]}"}])
    text = " ".join(str(part.get("text", "")) for part in parts).lower()
    declared = {
        declaration.get("name")
        for tool in body.get("tools") or []
        for declaration in (
            tool.get("functionDeclarations") or tool.get("function_declarations") or []
        )
    }
    if "time" in text and "current_datetime" in declared:
        call = {"name": "current_datetime", "args": {"timezone": "Asia/Karachi"}}
        return _gemini_answer([{"functionCall": call}])
    return _gemini_answer([{"text": "At your service, sir."}])


class _StandInGemini(BaseHTTPRequestHandler):
    """Google's Gemini API, scripted. Records every request for the checks."""

    # Every request as (path, API key sent, body).
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def log_message(self, *args: Any) -> None:  # keep the CI log readable
        pass

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, reason: str) -> None:
        self._send(
            {"error": {"code": status, "message": message, "status": reason}},
            status=status,
        )

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        _StandInGemini.requests.append(
            (self.path, self.headers.get("x-goog-api-key") or "", {})
        )
        self._error(404, f"The stand-in does not serve GET {self.path}", "NOT_FOUND")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("x-goog-api-key") or ""
        _StandInGemini.requests.append((self.path, key, body))
        if not self.path.startswith(f"/v1beta/models/{GEMINI_MODEL}:generateContent"):
            self._error(404, f"Unexpected request {self.path}", "NOT_FOUND")
            return
        users = [c for c in body.get("contents") or [] if c.get("role") == "user"]
        last_user = json.dumps(users[-1]).lower() if users else ""
        if key != GEMINI_TEST_KEY or REJECT_PHRASE in last_user:
            self._error(400, GEMINI_KEY_ERROR, "INVALID_ARGUMENT")
            return
        self._send(gemini_reply(body))


def start_stand_in_gemini() -> tuple[ThreadingHTTPServer, int]:
    _StandInGemini.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StandInGemini)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(
    url: str, payload: dict[str, Any] | None = None, timeout: float = 120
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_bytes(
    url: str,
    body: bytes,
    content_type: str,
    timeout: float,
) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), dict(response.headers.items())


def hud_chat(
    base: str, model: str, text: str, timeout: float = 180
) -> tuple[int, dict[str, str], Any]:
    """One turn as the JARVIS HUD sends it: non-streaming, from the app origin.

    Returns ``(status, headers, body)`` for error replies too, since the HUD
    reads those: a failed request must still be JSON with CORS headers.
    """
    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": APP_ORIGIN},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        status, headers, raw = exc.code, exc.headers, exc.read()
    try:
        body: Any = json.loads(raw.decode("utf-8"))
    except ValueError:
        body = raw.decode("utf-8", errors="replace")
    return status, {k.lower(): v for k, v in headers.items()}, body


def multipart(fields: dict[str, str], file_name: str, data: bytes) -> tuple[bytes, str]:
    boundary = "jarvis-smoke-boundary"
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{file_name}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def run(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def backend_env(home: Path, *, utf8: bool = True) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in CLOUD_KEYS}
    env.pop("JARVIS_NUM_CTX", None)  # the app's default context, as on a PC
    env["OPENJARVIS_HOME"] = str(home)
    env.pop("VIRTUAL_ENV", None)
    if utf8:
        env["PYTHONUTF8"] = "1"  # what the desktop app sets for the backend
        env.pop("PYTHONIOENCODING", None)
    else:
        # Redirected output on a Windows PC with a Western code page.
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp1252"
    return env


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

TOOL_CHECK = r"""
import json, sys
from openjarvis.tools.pc_control import CurrentDateTimeTool, SystemStatsTool
now = CurrentDateTimeTool().execute(timezone="Asia/Karachi")
assert now.success and "Asia/Karachi" in now.content, now.content
stats = SystemStatsTool().execute()
assert stats.success, stats.content
data = json.loads(stats.content)
assert data["memory"]["total_gb"] > 0, data
out = {"time": json.loads(now.content)["summary"], "stats": data["summary"]}
if sys.platform == "win32":
    from openjarvis.tools import pc_control
    out["start_menu_apps"] = len(pc_control._windows_start_apps())
    out["start_menu_shortcuts"] = len(pc_control._windows_start_menu_shortcuts())
print(json.dumps(out))
"""


def check_imports(python: list[str], root: Path, env: dict[str, str]) -> None:
    for module in IMPORTS:
        result = run(
            [*python, "-c", f"import {module}"], cwd=root, env=env, timeout=600
        )
        if result.returncode != 0:
            raise SmokeFailure(f"`import {module}` failed:\n{tail(result.stderr)}")
        print(f"  ok  import {module}", flush=True)
    if sys.platform == "win32":
        result = run([*python, "-c", "import tzdata"], cwd=root, env=env, timeout=120)
        if result.returncode != 0:
            raise SmokeFailure(f"`import tzdata` failed:\n{tail(result.stderr)}")


def check_listen(python: list[str], root: Path, home: Path) -> None:
    # Redirected output in a narrow code page must not crash the CLI.
    env = backend_env(home, utf8=False)
    result = run(
        [*python, "-m", "openjarvis.cli", "listen", "--check"],
        cwd=root,
        env=env,
        timeout=300,
    )
    if result.returncode != 0 or "JARVIS systems check" not in result.stdout:
        raise SmokeFailure(
            "`jarvis listen --check` failed:\n"
            + tail(result.stdout + "\n" + result.stderr)
        )
    print(tail(result.stdout, 1500), flush=True)


def check_tools(python: list[str], root: Path, env: dict[str, str]) -> None:
    result = run([*python, "-c", TOOL_CHECK], cwd=root, env=env, timeout=300)
    if result.returncode != 0:
        raise SmokeFailure(f"PC tools failed:\n{tail(result.stderr)}")
    print("  ok  PC tools: " + result.stdout.strip(), flush=True)


def check_voice(base: str, server_log: Any) -> None:
    """Speak in the British voice, then hear it back: the HUD's speech path.

    The first call downloads the Kokoro and Whisper models and, through pip,
    spaCy's English model.
    """
    for path, what in (
        ("/v1/speech/health", "Speech recognition"),
        ("/v1/speech/tts/health", "The voice"),
    ):
        health = http_json(f"{base}{path}")
        if not health.get("available"):
            raise SmokeFailure(f"{what} is unavailable: {health}")

    sentence = "Good evening, sir. All systems are online."
    try:
        audio, headers = http_post_bytes(
            f"{base}/v1/speech/synthesize",
            json.dumps({"text": sentence}).encode("utf-8"),
            "application/json",
            timeout=1200,
        )
    except urllib.error.HTTPError as exc:
        raise SmokeFailure(
            f"Speaking failed (HTTP {exc.code}). Server log:\n{server_log()}"
        ) from None
    voice = headers.get("X-Voice-Id") or headers.get("x-voice-id")
    if not audio.startswith(b"RIFF") or voice != "bm_george":
        raise SmokeFailure(
            f"Unexpected speech output (voice {voice!r}, {len(audio)} bytes)"
        )
    print(f"  ok  spoke {len(audio)} bytes of WAV as {voice}", flush=True)
    body, content_type = multipart(
        {"prompt": "Jarvis.", "vad": "1"}, "utterance.wav", audio
    )
    try:
        reply, _ = http_post_bytes(
            f"{base}/v1/speech/transcribe", body, content_type, timeout=1200
        )
    except urllib.error.HTTPError as exc:
        raise SmokeFailure(
            f"Transcribing failed (HTTP {exc.code}). Server log:\n{server_log()}"
        ) from None
    heard = str(json.loads(reply.decode("utf-8")).get("text", ""))
    print(f"  ok  heard: {heard!r}", flush=True)
    if "evening" not in heard.lower():
        report_warning("speech", f"Transcript differs from {sentence!r}: {heard!r}")


def check_low_memory_fallback(chats: list[tuple[int | None, str | None]]) -> None:
    """The server shrank the context when the model did not fit in memory."""
    refused = [ctx for ctx, error in chats if error == LOW_MEMORY_ERROR]
    answered = [ctx for ctx, error in chats if error is None]
    if not refused:
        raise SmokeFailure(
            f"The stand-in never refused a context above {STAND_IN_FITTING_CTX}; "
            f"the requests asked for {sorted({c for c, _ in chats})}"
        )
    if not answered or any((ctx or 0) > STAND_IN_FITTING_CTX for ctx in answered):
        raise SmokeFailure(f"The server did not shrink the context: {chats}")
    # The fitting size is remembered: one refusal per model load, not per turn.
    if len(refused) > 2:
        raise SmokeFailure(f"Every turn hit the memory limit again: {chats}")
    print(
        f"  ok  low memory: a {max(refused)}-token context did not fit; "
        f"answered {len(answered)} requests at {max(c or 0 for c in answered)}",
        flush=True,
    )


def check_failure_reaches_hud(base: str, model: str) -> None:
    """A failing brain is reported to the app, not hidden as a network error."""
    code, headers, body = hud_chat(base, model, f"Jarvis, {CRASH_PHRASE}")
    detail = body.get("detail") if isinstance(body, dict) else None
    problems = []
    if code < 500:
        problems.append(f"HTTP {code}")
    if headers.get("access-control-allow-origin") != APP_ORIGIN:
        problems.append("no CORS header, so the webview sees 'Failed to fetch'")
    if not isinstance(detail, str) or CRASH_ERROR not in detail:
        problems.append(f"the reply does not say what failed: {body!r}")
    if problems:
        raise SmokeFailure(
            "A failed chat must reach the app as a readable error: "
            + "; ".join(problems)
        )
    print(f"  ok  a failing brain is reported: HTTP {code} {detail}", flush=True)


def wait_until_healthy(
    server: subprocess.Popen[bytes], base: str, log_path: Path, label: str
) -> None:
    deadline = time.monotonic() + 420
    while True:
        if server.poll() is not None:
            raise SmokeFailure(
                f"{label} exited with {server.returncode}:\n"
                + tail(log_path.read_text(encoding="utf-8", errors="replace"))
            )
        try:
            http_json(f"{base}/health", timeout=5)
            return
        except (urllib.error.URLError, OSError, ValueError):
            if time.monotonic() > deadline:
                raise SmokeFailure(
                    f"{label} did not become healthy in 7 minutes:\n"
                    + tail(log_path.read_text(encoding="utf-8", errors="replace"))
                ) from None
            time.sleep(2)


def converse_with_gemini(python: list[str], root: Path, home: Path) -> None:
    """Run the server as the app's cloud source does: Gemini, Ollama off.

    The app starts ``jarvis serve`` with ``--engine cloud --brain gemini`` and
    the saved key in ``GEMINI_API_KEY``; nothing listens on Ollama's port.
    """
    stand_in, gemini_port = start_stand_in_gemini()
    ollama_port = free_port()  # nothing listens here: Ollama is off
    (home / "config.toml").write_text(
        "\n".join(
            [
                "[engine.ollama]",
                f'host = "http://127.0.0.1:{ollama_port}"',
                "",
                "[analytics]",
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    port = free_port()
    log_path = home / "serve-gemini.log"
    env = backend_env(home)
    env["GEMINI_API_KEY"] = GEMINI_TEST_KEY
    env["GOOGLE_GEMINI_BASE_URL"] = f"http://127.0.0.1:{gemini_port}"
    command = [
        *python,
        "-m",
        "openjarvis.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        *CLOUD_SOURCE_ARGS,
    ]
    label = "`jarvis serve " + " ".join(CLOUD_SOURCE_ARGS) + "`"
    print("$ " + " ".join(command), flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        server = subprocess.Popen(
            command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT
        )
    base = f"http://127.0.0.1:{port}"

    def server_log() -> str:
        return tail(log_path.read_text(encoding="utf-8", errors="replace"), 4000)

    try:
        wait_until_healthy(server, base, log_path, label)
        status = http_json(f"{base}/v1/jarvis/status")
        if status.get("model") != GEMINI_MODEL or not status.get("jarvis_mode"):
            raise SmokeFailure(
                f"The cloud source should make Gemini JARVIS's brain: {status}"
            )
        print(f"  ok  cloud source: brain={status['model']}, Ollama off", flush=True)

        text = "Jarvis, what time is it in Karachi?"
        code, headers, reply = hud_chat(base, GEMINI_MODEL, text)
        if code != 200:
            raise SmokeFailure(
                f"{text!r} failed with HTTP {code}: {reply}\n{server_log()}"
            )
        if headers.get("access-control-allow-origin") != APP_ORIGIN:
            raise SmokeFailure(f"The Gemini reply has no CORS header: {headers}")
        answer = reply["choices"][0]["message"]["content"] or ""
        print(f"  you: {text}\n  jarvis (gemini): {answer}", flush=True)
        if "Asia/Karachi" not in answer:
            raise SmokeFailure(
                f"JARVIS did not use current_datetime through Gemini: {answer!r}\n"
                + server_log()
            )
        chats = [r for r in _StandInGemini.requests if ":generateContent" in r[0]]
        if not chats or any(key != GEMINI_TEST_KEY for _, key, _ in chats):
            raise SmokeFailure(
                "The saved Gemini key did not reach Google's API: "
                f"{[(path, bool(key)) for path, key, _ in _StandInGemini.requests]}"
            )
        if not any("functionResponse" in json.dumps(body) for _, _, body in chats):
            raise SmokeFailure("The tool's result was never sent back to Gemini")
        print(f"  ok  {len(chats)} Gemini requests carried the saved key", flush=True)

        code, headers, body = hud_chat(base, GEMINI_MODEL, f"Jarvis, {REJECT_PHRASE}")
        detail = body.get("detail") if isinstance(body, dict) else None
        problems = []
        if code < 400:
            problems.append(f"HTTP {code}")
        if headers.get("access-control-allow-origin") != APP_ORIGIN:
            problems.append("no CORS header, so the webview sees 'Failed to fetch'")
        if not isinstance(detail, str) or "api key" not in detail.lower():
            problems.append(f"the reply does not mention the key: {body!r}")
        if problems:
            raise SmokeFailure(
                "A rejected Gemini key must reach the app as a readable error: "
                + "; ".join(problems)
            )
        print(f"  ok  a rejected key is reported: HTTP {code} {detail}", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        stand_in.shutdown()


def converse(python: list[str], root: Path, home: Path, *, voice: bool) -> None:
    stand_in, ollama_port = start_stand_in_ollama()
    (home / "config.toml").write_text(
        "\n".join(
            [
                "[engine]",
                'default = "ollama"',
                "",
                "[engine.ollama]",
                f'host = "http://127.0.0.1:{ollama_port}"',
                "",
                "[analytics]",
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    port = free_port()
    log_path = home / "serve.log"
    env = backend_env(home)
    command = [
        *python,
        "-m",
        "openjarvis.cli",
        "serve",
        "--agent",
        "jarvis",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    print("$ " + " ".join(command), flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        server = subprocess.Popen(
            command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT
        )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_until_healthy(server, base, log_path, "`jarvis serve --agent jarvis`")

        status = http_json(f"{base}/v1/jarvis/status")
        problems = []
        if status.get("agent") != "jarvis" or not status.get("jarvis_mode"):
            problems.append(f"agent is {status.get('agent')!r}")
        if status.get("model") != STAND_IN_MODEL:
            problems.append(f"brain is {status.get('model')!r}")
        for tool in ("open_app", "system_stats", "current_datetime", "memory_manage"):
            if tool not in (status.get("tools") or []):
                problems.append(f"tool {tool} missing")
        if problems:
            raise SmokeFailure(f"/v1/jarvis/status: {'; '.join(problems)}: {status}")
        print(
            f"  ok  status: agent={status['agent']} brain={status['model']} "
            f"tools={len(status['tools'])} voice={status.get('voice')}",
            flush=True,
        )

        def server_log() -> str:
            return tail(log_path.read_text(encoding="utf-8", errors="replace"), 4000)

        if voice:
            check_voice(base, server_log)
        else:
            print("  --  voice checks skipped", flush=True)

        def ask(text: str) -> str:
            code, headers, reply = hud_chat(base, status["model"], text)
            if code != 200:
                raise SmokeFailure(
                    f"{text!r} failed with HTTP {code}: {reply}\n{server_log()}"
                )
            if headers.get("access-control-allow-origin") != APP_ORIGIN:
                raise SmokeFailure(
                    f"The reply has no CORS header for {APP_ORIGIN}, so the "
                    f"app's webview could not read it: {headers}"
                )
            answer = reply["choices"][0]["message"]["content"] or ""
            print(f"  you: {text}\n  jarvis: {answer}", flush=True)
            lowered = answer.lower()
            if any(marker in lowered for marker in DENIAL_MARKERS):
                raise SmokeFailure(f"A JARVIS tool was blocked for {text!r}: {answer}")
            return answer

        if "Asia/Karachi" not in ask("Jarvis, what time is it in Karachi?"):
            raise SmokeFailure("current_datetime did not answer for Asia/Karachi")
        ask("Jarvis, remember that my name is Nova")
        profile = home / "USER.md"
        if not profile.is_file() or "Name: Nova" not in profile.read_text(
            encoding="utf-8"
        ):
            raise SmokeFailure(f"user_profile_manage did not save to {profile}")
        if "Nova" not in ask("Jarvis, what is my name?"):
            raise SmokeFailure("The saved profile was not in JARVIS's prompt")
        if sys.platform == "win32":
            answer = ask("Jarvis, open notepad")
            if "notepad" not in answer.lower():
                # The build machine has no interactive desktop; a launch error
                # here is not a JARVIS bug, but a security denial (raised in
                # ask) would be.
                report_warning("open_app", f"Notepad did not open: {answer}")
            subprocess.run(
                ["taskkill", "/IM", "notepad.exe", "/F"],
                capture_output=True,
                check=False,
            )
        check_low_memory_fallback(_StandInOllama.chats)
        check_failure_reaches_hud(base, status["model"])
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        stand_in.shutdown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # This script relays output that may contain symbols (✓, ✗) the console's
    # code page lacks; never crash on printing them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--backend", type=Path, help="staged backend to copy and sync")
    source.add_argument("--project", type=Path, help="use this project in place")
    parser.add_argument("--uv", default="uv", help="uv executable")
    parser.add_argument(
        "--python",
        help="interpreter to run checks with (default: the synced project's)",
    )
    parser.add_argument("--skip-imports", action="store_true")
    parser.add_argument(
        "--skip-voice",
        action="store_true",
        help="skip speaking and transcribing (no speech backends installed)",
    )
    parser.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="jarvis-smoke-"))
    home = work / "home"
    home.mkdir()
    failed = False
    try:
        if args.backend is not None:
            root = work / "backend"
            shutil.copytree(args.backend, root)
            result = run(
                [args.uv, *SYNC_COMMAND],
                cwd=root,
                env=backend_env(home),
                timeout=1800,
            )
            if result.returncode != 0:
                raise SmokeFailure(
                    "The desktop app's install command failed: uv "
                    + " ".join(SYNC_COMMAND)
                    + "\n"
                    + tail(result.stderr, 4000)
                )
            print("  ok  uv " + " ".join(SYNC_COMMAND), flush=True)
        else:
            root = args.project.resolve()

        if args.python:
            python = [args.python]
        else:
            # The synced venv's interpreter, not `uv run`: stopping `uv run`
            # on Windows can leave the server it started running.
            scripts = "Scripts" if sys.platform == "win32" else "bin"
            exe = "python.exe" if sys.platform == "win32" else "python"
            python = [str(root / ".venv" / scripts / exe)]
        env = backend_env(home)
        if not args.skip_imports:
            check_imports(python, root, env)
        check_listen(python, root, home)
        check_tools(python, root, env)
        converse(python, root, home, voice=not args.skip_voice)
        converse_with_gemini(python, root, home)
    except SmokeFailure as exc:
        report_failure("JARVIS backend smoke test", str(exc))
        failed = True
    except Exception as exc:  # report anything unexpected with context
        report_failure("JARVIS backend smoke test", f"{type(exc).__name__}: {exc}")
        failed = True
    finally:
        if args.keep:
            print(f"Kept {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    if failed:
        return 1
    print("JARVIS backend smoke test passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
