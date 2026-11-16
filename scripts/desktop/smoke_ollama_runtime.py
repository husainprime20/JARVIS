"""Run a real model through the Ollama that the Windows installer bundles.

The app ships ``ollama.exe`` as a sidecar plus Ollama's ``lib/ollama`` runtime
(``llama-server.exe`` and its DLLs) beside it. ``ollama.exe`` alone starts and
downloads models, but every chat then fails with "llama-server binary not
found", which a stand-in Ollama cannot catch. This check lays both out the way
the installer does, starts ``ollama serve`` the way the app does, downloads a
model and asks it a question.

Usage (the Windows EXE workflow runs this before packaging)::

    python scripts/desktop/smoke_ollama_runtime.py \\
        --ollama frontend/src-tauri/binaries/ollama-x86_64-pc-windows-msvc.exe \\
        --runtime frontend/src-tauri/binaries/ollama-runtime-x86_64-pc-windows-msvc \\
        --model qwen3.5:0.8b

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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
# The installer must not grow by Ollama's CUDA libraries (over 1 GB) unnoticed.
DEFAULT_MAX_RUNTIME_MB = 600
# Windows status code of a process that could not load one of its DLLs.
STATUS_DLL_NOT_FOUND = 0xC0000135


class SmokeFailure(AssertionError):
    """The bundled Ollama cannot run a model."""


def report_failure(message: str) -> None:
    print(f"FAIL bundled Ollama: {message}", flush=True)
    if IN_ACTIONS:
        escaped = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=Bundled Ollama::{escaped}", flush=True)


def llama_server_name(platform: str = sys.platform) -> str:
    return "llama-server.exe" if platform == "win32" else "llama-server"


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def runtime_report(runtime: Path, platform: str = sys.platform) -> dict[str, Any]:
    """Describe a staged runtime: its llama-server, backends and size."""
    lib = runtime / "lib" / "ollama"
    server = lib / llama_server_name(platform)
    if not server.is_file():
        raise SmokeFailure(
            f"{server} is missing; the installer would ship an Ollama that cannot "
            "run models"
        )
    backends = {
        child.name: directory_size(child)
        for child in sorted(lib.iterdir())
        if child.is_dir()
    }
    return {
        "server": server,
        "total": directory_size(runtime),
        "files": sum(1 for f in runtime.rglob("*") if f.is_file()),
        "backends": backends,
    }


def check_runtime_size(report: dict[str, Any], max_mb: int) -> None:
    total_mb = report["total"] / 1_048_576
    if total_mb > max_mb:
        largest = ", ".join(
            f"{name} {size / 1_048_576:.0f} MB"
            for name, size in sorted(
                report["backends"].items(), key=lambda item: -item[1]
            )
        )
        raise SmokeFailure(
            f"The runtime is {total_mb:.0f} MB, over the {max_mb} MB budget "
            f"(backends: {largest or 'none'}). If Ollama renamed its CUDA folders, "
            "update the exclusions in frontend/src-tauri/scripts/download-ollama.sh"
        )


def assemble_install_layout(ollama: Path, runtime: Path, app_dir: Path) -> Path:
    """Copy the sidecar and runtime the way the installer places them."""
    app_dir.mkdir(parents=True, exist_ok=True)
    name = "ollama.exe" if ollama.suffix.lower() == ".exe" else "ollama"
    target = app_dir / name
    shutil.copy2(ollama, target)
    shutil.copytree(runtime, app_dir, dirs_exist_ok=True)
    return target


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(
    url: str, payload: dict[str, Any] | None = None, timeout: float = 30
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"{url} returned HTTP {exc.code}: {body}") from None


def pull(base: str, model: str, timeout: float) -> None:
    """Download *model*, printing coarse progress."""
    req = urllib.request.Request(
        f"{base}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    last_status, last_decile = "", -1
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = f"Pulling {model} returned HTTP {exc.code}: {body}"
        raise SmokeFailure(message) from None
    with response:
        for raw in response:
            line = json.loads(raw.decode("utf-8") or "{}")
            if line.get("error"):
                raise SmokeFailure(f"Pulling {model} failed: {line['error']}")
            status = line.get("status", "")
            total, done = line.get("total") or 0, line.get("completed") or 0
            if total and status.startswith("pulling"):
                decile = int(done * 10 / total)
                if decile != last_decile:
                    last_decile = decile
                    print(f"      {status[:28]} {decile * 10}%", flush=True)
            elif status != last_status:
                print(f"      {status}", flush=True)
            last_status = status
    if last_status != "success":
        raise SmokeFailure(f"Pulling {model} ended with {last_status!r}")


def check_llama_server_starts(server: Path) -> None:
    """``--version`` fails fast when one of its DLLs is missing."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(server), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        **kwargs,
    )
    if result.returncode != 0:
        code = result.returncode & 0xFFFFFFFF
        hint = " (a DLL it needs is missing)" if code == STATUS_DLL_NOT_FOUND else ""
        raise SmokeFailure(
            f"{server.name} --version exited with {code:#x}{hint}:\n"
            f"{(result.stdout + result.stderr)[-2000:]}"
        )
    version = (result.stdout + result.stderr).strip().splitlines()
    print(f"  ok  {server.name}: {version[0] if version else 'started'}", flush=True)


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop ollama and the llama-server it started."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def tail(path: Path, lines: int = 60) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def run_model(ollama_exe: Path, model: str, work: Path) -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = work / "ollama.log"
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}"}
    env["OLLAMA_MODELS"] = str(work / "models")
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        # As the app starts it: a hidden console, output not shown.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    print(f"$ {ollama_exe} serve  (OLLAMA_HOST=127.0.0.1:{port})", flush=True)
    with log_path.open("wb") as log:
        # Started from another folder: the app's working directory is arbitrary.
        process = subprocess.Popen(
            [str(ollama_exe), "serve"],
            cwd=work,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                raise SmokeFailure(
                    f"ollama serve exited with {process.returncode}:\n{tail(log_path)}"
                )
            try:
                version = request(f"{base}/api/version", timeout=5)
                break
            except (urllib.error.URLError, OSError, ValueError):
                if time.monotonic() > deadline:
                    raise SmokeFailure(
                        f"ollama serve did not answer in 60 s:\n{tail(log_path)}"
                    ) from None
                time.sleep(1)
        print(f"  ok  ollama {version.get('version')} is serving", flush=True)

        started = time.monotonic()
        try:
            pull(base, model, timeout=1800)
        except (urllib.error.URLError, OSError) as exc:
            raise SmokeFailure(f"Pulling {model} failed: {exc}") from None
        print(f"  ok  pulled {model} in {time.monotonic() - started:.0f} s", flush=True)

        started = time.monotonic()
        try:
            reply = request(
                f"{base}/api/chat",
                {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "Reply with one word: ready"}
                    ],
                    "stream": False,
                    "think": False,
                    "options": {"num_ctx": 4096, "num_predict": 64, "temperature": 0},
                },
                timeout=600,
            )
        except (SmokeFailure, urllib.error.URLError, OSError) as exc:
            raise SmokeFailure(f"The chat failed: {exc}\n{tail(log_path)}") from None
        answer = ((reply.get("message") or {}).get("content") or "").strip()
        if not answer:
            message = f"{model} gave an empty reply: {reply}"
            raise SmokeFailure(f"{message}\n{tail(log_path)}")
        tokens = reply.get("eval_count") or 0
        seconds = (reply.get("eval_duration") or 0) / 1e9
        speed = f", {tokens / seconds:.0f} tokens/s" if seconds else ""
        print(
            f"  ok  {model} answered {answer[:80]!r} in "
            f"{time.monotonic() - started:.1f} s{speed}",
            flush=True,
        )
        for loaded in request(f"{base}/api/ps").get("models") or []:
            size, vram = loaded.get("size") or 0, loaded.get("size_vram") or 0
            where = f"{vram * 100 // size}% GPU" if size and vram else "CPU"
            print(f"  ok  {loaded.get('name')} runs on {where}", flush=True)
    finally:
        stop(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ollama", type=Path, required=True, help="sidecar binary")
    parser.add_argument(
        "--runtime", type=Path, required=True, help="staged runtime folder"
    )
    parser.add_argument("--model", default="qwen3.5:0.8b", help="model to run")
    parser.add_argument(
        "--max-runtime-mb",
        type=int,
        default=DEFAULT_MAX_RUNTIME_MB,
        help="fail when the runtime is larger than this",
    )
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="only check the files (no server, no download)",
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    work = Path(tempfile.mkdtemp(prefix="ollama-smoke-"))
    try:
        report = runtime_report(args.runtime)
        backends = ", ".join(
            f"{name} ({size / 1_048_576:.0f} MB)"
            for name, size in report["backends"].items()
        )
        print(
            f"  ok  runtime: {report['total'] / 1_048_576:.0f} MB in "
            f"{report['files']} files; GPU backends: {backends or 'none'}",
            flush=True,
        )
        check_runtime_size(report, args.max_runtime_mb)
        ollama_exe = assemble_install_layout(args.ollama, args.runtime, work / "app")
        check_llama_server_starts(
            work / "app" / "lib" / "ollama" / report["server"].name
        )
        if args.skip_model:
            print("  --  model run skipped", flush=True)
        else:
            run_model(ollama_exe, args.model, work)
    except SmokeFailure as exc:
        report_failure(str(exc))
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("Bundled Ollama smoke test passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
