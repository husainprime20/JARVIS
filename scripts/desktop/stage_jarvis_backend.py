"""Stage the Python backend that the JARVIS desktop installer bundles.

The Windows installer ships this folder as ``<install dir>/src``, which is the
first place the desktop app looks for a backend (before cloning one). It holds
the tracked backend sources plus a prebuilt ``openjarvis_rust`` wheel, so the
app's first launch needs no git, Rust toolchain or Visual Studio Build Tools:
it only runs ``uv sync --extra jarvis --group desktop-native`` there.

Usage (from the repository root, after building the wheel with maturin)::

    python scripts/desktop/stage_jarvis_backend.py \\
        --wheel dist/wheels/openjarvis_rust-0.1.0-cp312-cp312-win_amd64.whl \\
        --out frontend/src-tauri/jarvis-backend

The staged ``pyproject.toml`` points ``openjarvis-rust`` at the wheel and pins
Python to the wheel's version (the extension is not built for the stable ABI),
then ``uv lock`` refreshes the lockfile to match.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Tracked paths the backend needs at install and run time. Rust sources are
# left out on purpose: the prebuilt wheel replaces them.
INCLUDE = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "src/openjarvis",
    # Starter configs used by `jarvis init --preset ...`.
    "configs",
    # Force-included into the package by [tool.hatch.build.targets.wheel].
    "scripts/install",
    "deploy/windows",
)

WHEEL_DIR = "wheels"

_RUST_SOURCE = re.compile(r"^openjarvis-rust\s*=\s*\{[^}\n]*\}[ \t]*$", re.MULTILINE)
_REQUIRES_PYTHON = re.compile(r'^requires-python\s*=\s*"[^"\n]*"[ \t]*$', re.MULTILINE)
_WHEEL_PYTHON_TAG = re.compile(r"^cp(\d)(\d+)$")


class StagingError(RuntimeError):
    """The backend could not be staged."""


def wheel_python_version(wheel_name: str) -> tuple[int, int]:
    """Return the CPython ``(major, minor)`` a wheel was built for."""
    parts = Path(wheel_name).stem.split("-")
    if len(parts) < 5:
        raise StagingError(f"Not a wheel file name: {wheel_name}")
    match = _WHEEL_PYTHON_TAG.match(parts[-3])
    if match is None:
        raise StagingError(
            f"{wheel_name} is not a CPython-specific wheel (tag {parts[-3]!r})"
        )
    return int(match.group(1)), int(match.group(2))


def point_rust_source_at_wheel(pyproject: str, wheel_path: str) -> str:
    """Make ``[tool.uv.sources] openjarvis-rust`` install *wheel_path*."""
    replacement = f'openjarvis-rust = {{ path = "{wheel_path}" }}'
    updated, count = _RUST_SOURCE.subn(replacement, pyproject)
    if count != 1:
        raise StagingError(
            "Expected exactly one `openjarvis-rust = { ... }` entry in "
            f"[tool.uv.sources], found {count}."
        )
    return updated


def pin_python(pyproject: str, major: int, minor: int) -> str:
    """Restrict ``requires-python`` to one minor version."""
    replacement = f'requires-python = ">={major}.{minor}, <{major}.{minor + 1}"'
    updated, count = _REQUIRES_PYTHON.subn(replacement, pyproject)
    if count != 1:
        raise StagingError(f"Expected one `requires-python` line, found {count}.")
    return updated


def tracked_files(repo: Path) -> list[str]:
    """Tracked files under :data:`INCLUDE`, relative to *repo*."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *INCLUDE],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    files = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    if "pyproject.toml" not in files or "uv.lock" not in files:
        raise StagingError(f"{repo} does not look like an OpenJarvis checkout.")
    return files


def _commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def stage(
    *,
    wheel: Path,
    out: Path,
    repo: Path = REPO_ROOT,
    uv: str = "uv",
    lock: bool = True,
) -> Path:
    """Build the staged backend in *out* (replaced if it exists)."""
    if not wheel.is_file():
        raise StagingError(f"Wheel not found: {wheel}")
    major, minor = wheel_python_version(wheel.name)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in tracked_files(repo):
        source = repo / name
        if not source.is_file():  # deleted in the working tree
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    (out / WHEEL_DIR).mkdir()
    shutil.copy2(wheel, out / WHEEL_DIR / wheel.name)

    pyproject = out / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = point_rust_source_at_wheel(text, f"{WHEEL_DIR}/{wheel.name}")
    text = pin_python(text, major, minor)
    pyproject.write_text(text, encoding="utf-8")
    (out / ".python-version").write_text(f"{major}.{minor}\n", encoding="utf-8")
    (out / "JARVIS_BACKEND.json").write_text(
        json.dumps(
            {
                "commit": _commit(repo),
                "wheel": wheel.name,
                "python": f"{major}.{minor}",
                "sync": "uv sync --extra jarvis --group desktop-native",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if lock:
        result = subprocess.run(
            [uv, "lock"], cwd=out, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            output = (result.stderr or result.stdout)[-3000:]
            raise StagingError(f"`uv lock` failed in {out}:\n{output}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--uv", default="uv", help="uv executable")
    parser.add_argument(
        "--no-lock", action="store_true", help="skip `uv lock` (tests only)"
    )
    args = parser.parse_args(argv)
    try:
        out = stage(
            wheel=args.wheel,
            out=args.out,
            repo=args.repo,
            uv=args.uv,
            lock=not args.no_lock,
        )
    except (StagingError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    files = sum(1 for path in out.rglob("*") if path.is_file())
    print(f"Staged JARVIS backend in {out} ({files} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
