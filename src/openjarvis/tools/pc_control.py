"""Desktop-control tools for JARVIS mode.

These tools let a voice assistant act on the computer it runs on: launch apps,
open websites, play YouTube, press media keys, report system health, and tell
the time. Windows is the primary target; macOS and Linux get best-effort
equivalents.

None of these tools executes model-supplied text as a command:

* ``open_app`` launches only apps it can resolve from a fixed table, the
  user's own ``[jarvis.apps]`` config, or the operating system's list of
  installed apps (Windows Start menu, macOS ``/Applications``, Linux
  ``.desktop`` entries). The requested name is used for matching only.
* ``open_website`` and ``play_youtube`` hand only ``http(s)`` URLs to the
  default browser.
* ``system_control`` maps a closed set of actions to fixed key codes or
  fixed helper commands.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generic, Iterable, List, Optional, Tuple, TypeVar
from urllib.parse import quote_plus, urlsplit, urlunsplit

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_MAX_NAME_LENGTH = 100
_MAX_URL_LENGTH = 2048
_MAX_QUERY_LENGTH = 200
_START_APPS_TTL_SECONDS = 300.0
_MAX_SCANNED_ENTRIES = 5000
_MATCH_THRESHOLD = 0.72

# ---------------------------------------------------------------------------
# Platform seams (module-level so tests can replace them)
# ---------------------------------------------------------------------------


def _current_platform() -> str:
    """Return ``sys.platform``; wrapped so tests can simulate other OSes."""
    return sys.platform


def _startfile(target: str) -> None:
    """Open *target* with the Windows shell (``ShellExecute``)."""
    startfile = getattr(os, "startfile", None)
    if startfile is None:  # pragma: no cover - only reachable off Windows
        raise OSError("os.startfile is only available on Windows")
    startfile(target)


def _popen(args: List[str]) -> None:
    """Start a detached GUI helper process without a console window."""
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if _current_platform() == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)  # noqa: S603 - fixed argv, never a shell


def _run(args: List[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a short helper command and capture its output."""
    kwargs: Dict[str, Any] = {}
    if _current_platform() == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(  # noqa: S603 - fixed argv, never a shell
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )


def _open_url(url: str) -> bool:
    """Open *url* in a new tab of the default browser."""
    return webbrowser.open(url, new=2)


def _failure(tool_name: str, message: str) -> ToolResult:
    return ToolResult(tool_name=tool_name, content=message, success=False)


def _validate_text(value: Any, *, field: str, max_length: int) -> str:
    """Return a stripped, printable string or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if len(text) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{field} must not contain control characters.")
    return text


# ---------------------------------------------------------------------------
# Fuzzy name matching
# ---------------------------------------------------------------------------

_FILLER_WORDS = frozenset({"the", "app", "application", "program", "my", "please"})
# Start-menu folders are full of uninstallers and help links that share the
# app's name; never launch one unless the user explicitly asked for it.
_NOISE_WORDS = (
    "uninstall",
    "readme",
    "release notes",
    "help",
    "documentation",
    "manual",
    "website",
    "license",
    "changelog",
    "safe mode",
)


def _normalize_name(value: str) -> str:
    """Lowercase *value* and reduce it to comparable words."""
    text = value.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#.\s]", " ", text)
    words = [word.strip(".") for word in text.split()]
    return " ".join(word for word in words if word and word not in _FILLER_WORDS)


def _match_score(query: str, candidate: str) -> float:
    """Score how well *candidate* names the app the user asked for."""
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if query.replace(" ", "") == candidate.replace(" ", ""):
        return 0.95
    if candidate.startswith(query + " ") or query.startswith(candidate + " "):
        return 0.9
    if set(query.split()) <= set(candidate.split()):
        return 0.85
    return difflib.SequenceMatcher(None, query, candidate).ratio() * 0.9


@dataclass(frozen=True)
class _Match(Generic[_T]):
    label: str
    value: _T
    score: float


def _best_match(
    query: str,
    entries: Iterable[Tuple[str, _T]],
    *,
    threshold: float = _MATCH_THRESHOLD,
) -> Optional[_Match[_T]]:
    """Return the best ``(label, value)`` entry for *query*, if any is close."""
    best: Optional[_Match[_T]] = None
    for label, value in entries:
        candidate = _normalize_name(label)
        if any(noise in candidate and noise not in query for noise in _NOISE_WORDS):
            continue
        score = _match_score(query, candidate)
        if score < threshold:
            continue
        if (
            best is None
            or score > best.score
            or (score == best.score and len(label) < len(best.label))
        ):
            best = _Match(label=label, value=value, score=score)
    return best


# ---------------------------------------------------------------------------
# App launching
# ---------------------------------------------------------------------------


class AppNotFoundError(LookupError):
    """No installed app matched the requested name."""


# Built-in Windows apps and App Paths executables. Third-party apps that use
# URI schemes (Spotify, WhatsApp, ...) are deliberately absent: when they are
# not installed, ShellExecute shows a "find an app" dialog instead of failing,
# so they are resolved from the Start menu list instead.
_WINDOWS_APPS: Dict[str, Tuple[str, str]] = {
    "notepad": ("Notepad", "notepad.exe"),
    "calculator": ("Calculator", "calc.exe"),
    "paint": ("Paint", "mspaint.exe"),
    "wordpad": ("WordPad", "write.exe"),
    "command prompt": ("Command Prompt", "cmd.exe"),
    "powershell": ("PowerShell", "powershell.exe"),
    "windows terminal": ("Windows Terminal", "wt.exe"),
    "file explorer": ("File Explorer", "explorer.exe"),
    "task manager": ("Task Manager", "taskmgr.exe"),
    "control panel": ("Control Panel", "control.exe"),
    "settings": ("Settings", "ms-settings:"),
    "camera": ("Camera", "microsoft.windows.camera:"),
    "clock": ("Clock", "ms-clock:"),
    "microsoft store": ("Microsoft Store", "ms-windows-store:"),
    "photos": ("Photos", "ms-photos:"),
    "snipping tool": ("Snipping Tool", "ms-screenclip:"),
    "edge": ("Microsoft Edge", "msedge.exe"),
    "chrome": ("Google Chrome", "chrome.exe"),
    "firefox": ("Firefox", "firefox.exe"),
    "word": ("Microsoft Word", "winword.exe"),
    "excel": ("Microsoft Excel", "excel.exe"),
    "powerpoint": ("Microsoft PowerPoint", "powerpnt.exe"),
    "outlook": ("Microsoft Outlook", "outlook.exe"),
    "onenote": ("OneNote", "onenote.exe"),
    "vlc": ("VLC media player", "vlc.exe"),
}

_WINDOWS_ALIASES: Dict[str, str] = {
    "calc": "calculator",
    "cmd": "command prompt",
    "terminal": "windows terminal",
    "explorer": "file explorer",
    "files": "file explorer",
    "file manager": "file explorer",
    "this pc": "file explorer",
    "my computer": "file explorer",
    "task mgr": "task manager",
    "windows settings": "settings",
    "pc settings": "settings",
    "store": "microsoft store",
    "alarms": "clock",
    "alarm": "clock",
    "timer": "clock",
    "screenshot": "snipping tool",
    "microsoft edge": "edge",
    "google chrome": "chrome",
    "chrome browser": "chrome",
    "mozilla firefox": "firefox",
    "microsoft word": "word",
    "ms word": "word",
    "microsoft excel": "excel",
    "ms excel": "excel",
    "microsoft powerpoint": "powerpoint",
    "ms powerpoint": "powerpoint",
    "microsoft outlook": "outlook",
    "vlc player": "vlc",
    "vlc media player": "vlc",
    "browser": "edge",
    "web browser": "edge",
    # Not built-in targets: rewritten names for the Start-menu search.
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "teams": "microsoft teams",
}

_MAC_APPS: Dict[str, str] = {
    "browser": "Safari",
    "web browser": "Safari",
    "terminal": "Terminal",
    "command prompt": "Terminal",
    "calculator": "Calculator",
    "calc": "Calculator",
    "notepad": "TextEdit",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "file explorer": "Finder",
    "files": "Finder",
    "task manager": "Activity Monitor",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
}

_LINUX_ALIASES: Dict[str, str] = {
    "browser": "firefox",
    "web browser": "firefox",
    "chrome": "google chrome",
    "calc": "calculator",
    "file explorer": "files",
    "file manager": "files",
    "task manager": "system monitor",
    "command prompt": "terminal",
    "notepad": "text editor",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
}

_START_APPS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$apps = @(Get-StartApps | Select-Object Name, AppID); "
    "ConvertTo-Json -InputObject $apps -Compress"
)

_start_apps_cache: Tuple[float, List[Tuple[str, str]]] | None = None
_start_apps_lock = threading.Lock()


def _windows_start_apps() -> List[Tuple[str, str]]:
    """Return ``(name, AppID)`` for every Start-menu app (desktop and Store).

    ``Get-StartApps`` is the only source that also lists Store (UWP) apps such
    as WhatsApp or Spotify. The result is cached for a few minutes because
    starting PowerShell costs about a second.
    """
    global _start_apps_cache
    with _start_apps_lock:
        now = time.monotonic()
        if (
            _start_apps_cache is not None
            and now - _start_apps_cache[0] < _START_APPS_TTL_SECONDS
        ):
            return _start_apps_cache[1]
        apps: List[Tuple[str, str]] = []
        try:
            proc = _run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _START_APPS_SCRIPT,
                ],
                timeout=20.0,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                payload = json.loads(proc.stdout)
                if isinstance(payload, dict):
                    payload = [payload]
                for item in payload if isinstance(payload, list) else []:
                    if not isinstance(item, dict):
                        continue
                    name, app_id = item.get("Name"), item.get("AppID")
                    if isinstance(name, str) and isinstance(app_id, str) and app_id:
                        apps.append((name, app_id))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.debug("Get-StartApps failed: %s", exc)
        _start_apps_cache = (now, apps)
        return apps


def _windows_start_menu_shortcuts() -> List[Tuple[str, Path]]:
    """Fallback app list: shortcuts in the all-users and per-user Start menu."""
    roots = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        roots.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        roots.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    shortcuts: List[Tuple[str, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in {".lnk", ".url", ".appref-ms"}:
                shortcuts.append((path.stem, path))
                if len(shortcuts) >= _MAX_SCANNED_ENTRIES:
                    return shortcuts
    return shortcuts


def _mac_applications() -> List[Tuple[str, Path]]:
    roots = [
        Path("/Applications"),
        Path("/Applications/Utilities"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path.home() / "Applications",
    ]
    apps: List[Tuple[str, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*.app"):
            apps.append((path.stem, path))
    return apps


def _linux_desktop_entries() -> List[Tuple[str, Path]]:
    """Return ``(Name, path)`` for visible XDG ``.desktop`` launchers."""
    data_dirs = [
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"),
        *(
            Path(part)
            for part in (
                os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
            ).split(":")
            if part
        ),
        Path("/var/lib/flatpak/exports/share"),
        Path("/var/lib/snapd/desktop"),
    ]
    entries: List[Tuple[str, Path]] = []
    seen: set[str] = set()
    for data_dir in data_dirs:
        app_dir = data_dir / "applications"
        if not app_dir.is_dir():
            continue
        for path in app_dir.glob("*.desktop"):
            if path.name in seen:
                continue
            seen.add(path.name)
            name = ""
            hidden = False
            try:
                for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    if line.startswith("[") and line.strip() != "[Desktop Entry]":
                        break
                    if line.startswith("Name=") and not name:
                        name = line[5:].strip()
                    elif line.strip() in {"NoDisplay=true", "Hidden=true"}:
                        hidden = True
            except OSError:
                continue
            if name and not hidden:
                entries.append((name, path))
            if len(entries) >= _MAX_SCANNED_ENTRIES:
                return entries
    return entries


def _looks_like_uri(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) and not re.match(
        r"^[a-zA-Z]:[\\/]", target
    )


def _launch_configured(target: str, plat: str) -> None:
    """Launch a user-configured ``[jarvis.apps]`` target (trusted config).

    The target is a path, a URI such as ``spotify:``, or (macOS) an app name
    or (Linux) a command line. It comes from the user's own config file, never
    from the model.
    """
    if plat == "win32":
        _startfile(target)
        return
    expanded = Path(target).expanduser()
    if plat == "darwin":
        if _looks_like_uri(target):
            _popen(["open", target])
        elif expanded.exists():
            _popen(["open", str(expanded)])
        else:
            _popen(["open", "-a", target])
        return
    if _looks_like_uri(target):
        _popen(["xdg-open", target])
    elif expanded.is_file() and os.access(expanded, os.X_OK):
        _popen([str(expanded)])
    elif expanded.exists():
        _popen(["xdg-open", str(expanded)])
    else:
        _popen(shlex.split(target))


def _open_app_windows(query: str) -> str:
    key = _WINDOWS_ALIASES.get(query, query)
    known = _WINDOWS_APPS.get(key)
    if known is not None:
        label, target = known
        try:
            _startfile(target)
            return label
        except OSError as exc:
            # App Paths executables (chrome.exe, winword.exe, ...) raise when
            # the app is not installed; fall through to the Start-menu search.
            logger.debug("Built-in launch of %s failed: %s", target, exc)

    start_match = _best_match(key, _windows_start_apps())
    if start_match is not None:
        _popen(["explorer.exe", f"shell:AppsFolder\\{start_match.value}"])
        return start_match.label

    shortcut_match = _best_match(key, _windows_start_menu_shortcuts())
    if shortcut_match is not None:
        _startfile(str(shortcut_match.value))
        return shortcut_match.label

    raise AppNotFoundError(query)


def _open_app_mac(query: str) -> str:
    alias = _MAC_APPS.get(query)
    apps = _mac_applications()
    match = _best_match(_normalize_name(alias) if alias else query, apps)
    if match is not None:
        _popen(["open", "-a", str(match.value)])
        return match.label
    if alias is not None:
        _popen(["open", "-a", alias])
        return alias
    raise AppNotFoundError(query)


def _open_app_linux(query: str) -> str:
    wanted = _LINUX_ALIASES.get(query, query)
    match = _best_match(wanted, _linux_desktop_entries())
    if match is not None:
        desktop_id = match.value.name
        if shutil.which("gtk-launch"):
            _popen(["gtk-launch", desktop_id])
        elif shutil.which("gio"):
            _popen(["gio", "launch", str(match.value)])
        else:
            _popen(["xdg-open", str(match.value)])
        return match.label
    for candidate in (wanted.replace(" ", "-"), wanted.replace(" ", "")):
        executable = shutil.which(candidate)
        if executable:
            _popen([executable])
            return candidate
    raise AppNotFoundError(query)


def open_app_by_name(
    name: str, configured_apps: Optional[Dict[str, Any]] = None
) -> str:
    """Launch the app the user called *name*; return the app's display name.

    Raises :class:`AppNotFoundError` when nothing matches and ``OSError`` when
    the operating system refuses to start a matched app.
    """
    query = _normalize_name(name)
    if not query:
        raise AppNotFoundError(name)
    plat = _current_platform()

    for label, target in (configured_apps or {}).items():
        if (
            isinstance(label, str)
            and isinstance(target, str)
            and target.strip()
            and _normalize_name(label) == query
        ):
            _launch_configured(target.strip(), plat)
            return label

    if plat == "win32":
        return _open_app_windows(query)
    if plat == "darwin":
        return _open_app_mac(query)
    return _open_app_linux(query)


def _configured_apps(explicit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if explicit is not None:
        return explicit
    try:
        from openjarvis.core.config import load_config

        apps = getattr(getattr(load_config(), "jarvis", None), "apps", None)
    except Exception as exc:  # config errors must not break app launching
        logger.debug("Could not read [jarvis.apps]: %s", exc)
        return {}
    return apps if isinstance(apps, dict) else {}


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    """Launch an installed application by its everyday name."""

    tool_id = "open_app"
    is_local = True

    def __init__(self, *, apps: Optional[Dict[str, Any]] = None) -> None:
        self._apps = apps

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_app",
            description=(
                "Open (launch) an application on the user's computer by its "
                "everyday name, e.g. 'notepad', 'calculator', 'chrome', "
                "'spotify', 'vs code', 'file explorer' or 'settings'. Use "
                "open_website for web pages and play_youtube for videos or music."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The app name as the user said it.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            category="desktop",
            latency_estimate=1.0,
            timeout_seconds=30.0,
            required_capabilities=["code:execute"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            name = _validate_text(
                params.get("name"), field="name", max_length=_MAX_NAME_LENGTH
            )
        except ValueError as exc:
            return _failure("open_app", str(exc))
        if "://" in name or name.lower().startswith("www."):
            return _failure(
                "open_app", "That looks like a web address; use open_website."
            )
        if any(sep in name for sep in ("\\", "/")):
            return _failure(
                "open_app",
                "Give the app's name, not a file path. Custom programs can be "
                "added under [jarvis.apps] in the OpenJarvis config.",
            )
        try:
            label = open_app_by_name(name, _configured_apps(self._apps))
        except AppNotFoundError:
            return _failure(
                "open_app",
                f"I couldn't find an app called '{name}' on this computer. If "
                "it is installed, the user can add its path under [jarvis.apps] "
                "in the OpenJarvis config file.",
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return _failure("open_app", f"Could not open '{name}': {exc}")
        return ToolResult(
            tool_name="open_app",
            content=f"Opened {label}.",
            success=True,
            metadata={"app": label},
        )


# ---------------------------------------------------------------------------
# Websites and YouTube
# ---------------------------------------------------------------------------

_KNOWN_SITES: Dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "google docs": "https://docs.google.com",
    "google maps": "https://www.google.com/maps",
    "maps": "https://www.google.com/maps",
    "google calendar": "https://calendar.google.com",
    "google news": "https://news.google.com",
    "news": "https://news.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "wikipedia": "https://www.wikipedia.org",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "spotify": "https://open.spotify.com",
    "outlook": "https://outlook.live.com",
    "hotmail": "https://outlook.live.com",
}


def normalize_url(raw: str) -> str:
    """Turn a site name, bare domain or URL into a safe ``http(s)`` URL.

    Raises ``ValueError`` for anything that is not a plain web address.
    """
    text = _validate_text(raw, field="target", max_length=_MAX_URL_LENGTH)
    known = _KNOWN_SITES.get(_normalize_name(text))
    if known is not None:
        return known
    if any(char.isspace() for char in text):
        raise ValueError(
            "That is not a web address. Pass a site name such as 'youtube', a "
            "domain such as 'bbc.co.uk', or use search_query to search."
        )
    if "://" not in text:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) and not re.match(
            r"^[^:/]+:\d+", text
        ):
            raise ValueError("Only http and https links can be opened.")
        if "." not in text and not text.lower().startswith("localhost"):
            raise ValueError(
                f"'{text}' is not a known site. Pass the full domain, for "
                "example 'example.com', or use search_query to search."
            )
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only http and https links can be opened.")
    if not parts.hostname:
        raise ValueError("The web address has no host name.")
    if parts.username or parts.password:
        raise ValueError("Links with embedded credentials are not allowed.")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path, parts.query, parts.fragment)
    )


@ToolRegistry.register("open_website")
class OpenWebsiteTool(BaseTool):
    """Open a web page, or a web search, in the default browser."""

    tool_id = "open_website"
    # The URL leaves the machine, so outbound boundary scanning applies.
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_website",
            description=(
                "Open a website in the user's default web browser. Pass a site "
                "name ('youtube', 'gmail', 'github'), a domain ('bbc.co.uk') or "
                "a full https URL as target, or pass search_query instead to open "
                "a Google search. Use play_youtube to play a video or song."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Site name, domain, or full http(s) URL.",
                    },
                    "search_query": {
                        "type": "string",
                        "description": "Text to search for on Google instead.",
                    },
                },
                "additionalProperties": False,
            },
            category="desktop",
            latency_estimate=0.5,
            timeout_seconds=15.0,
            required_capabilities=["network:fetch", "code:execute"],
        )

    def execute(self, **params: Any) -> ToolResult:
        target = params.get("target")
        query = params.get("search_query")
        try:
            if query not in (None, ""):
                search = _validate_text(
                    query, field="search_query", max_length=_MAX_QUERY_LENGTH
                )
                url = f"https://www.google.com/search?q={quote_plus(search)}"
                spoken = f"Searching Google for '{search}'."
            elif target not in (None, ""):
                url = normalize_url(target)
                spoken = f"Opened {urlsplit(url).hostname}."
            else:
                return _failure(
                    "open_website", "Pass a target website or a search_query."
                )
        except ValueError as exc:
            return _failure("open_website", str(exc))

        try:
            opened = _open_url(url)
        except webbrowser.Error as exc:
            return _failure("open_website", f"Could not open a browser: {exc}")
        if not opened:
            return _failure("open_website", "No web browser is available.")
        return ToolResult(
            tool_name="open_website",
            content=spoken,
            success=True,
            metadata={"url": url},
        )


_YOUTUBE_RESULTS_URL = "https://www.youtube.com/results"
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
_VIDEO_RENDERER_RE = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"')
_WATCH_ID_RE = re.compile(r"watch\?v=([A-Za-z0-9_-]{11})")
_TITLE_RE = re.compile(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.){1,300})"')
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def find_youtube_video(query: str, *, timeout: float = 8.0) -> Tuple[str, str] | None:
    """Return ``(video_id, title)`` of the top YouTube result, or ``None``.

    Scrapes the public results page (the same thing the browser loads); the
    host is fixed, so no caller input reaches the request target.
    """
    try:
        response = httpx.get(
            _YOUTUBE_RESULTS_URL,
            params={"search_query": query, "hl": "en"},
            headers={
                "User-Agent": _BROWSER_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            # Skip the EU cookie-consent interstitial.
            cookies={"CONSENT": "YES+cb", "SOCS": "CAI"},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("YouTube search failed: %s", exc)
        return None
    html = response.text
    match = _VIDEO_RENDERER_RE.search(html) or _WATCH_ID_RE.search(html)
    if match is None:
        return None
    title = ""
    title_match = _TITLE_RE.search(html, match.end(), match.end() + 4000)
    if title_match is not None:
        try:
            title = json.loads(f'"{title_match.group(1)}"')
        except ValueError:
            title = ""
    return match.group(1), title


@ToolRegistry.register("play_youtube")
class PlayYouTubeTool(BaseTool):
    """Play the top YouTube result for a song or video request."""

    tool_id = "play_youtube"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="play_youtube",
            description=(
                "Play a video or song on YouTube in the user's browser. Pass "
                "what to play, e.g. 'lofi hip hop' or 'Back in Black AC/DC'. Set "
                "autoplay to false to just show the search results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Song, video, artist or topic to play.",
                    },
                    "autoplay": {
                        "type": "boolean",
                        "description": "Play the top result (default true).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            category="desktop",
            latency_estimate=2.0,
            timeout_seconds=20.0,
            required_capabilities=["network:fetch", "code:execute"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            query = _validate_text(
                params.get("query"), field="query", max_length=_MAX_QUERY_LENGTH
            )
        except ValueError as exc:
            return _failure("play_youtube", str(exc))
        autoplay = params.get("autoplay", True)
        if not isinstance(autoplay, bool):
            return _failure("play_youtube", "autoplay must be true or false.")

        url = f"{_YOUTUBE_RESULTS_URL}?search_query={quote_plus(query)}"
        spoken = f"Showing YouTube results for '{query}'."
        video_id = ""
        title = ""
        if autoplay:
            found = find_youtube_video(query)
            if found is not None:
                video_id, title = found
                url = _YOUTUBE_WATCH_URL.format(video_id=video_id)
                spoken = f"Playing '{title or query}' on YouTube."

        try:
            opened = _open_url(url)
        except webbrowser.Error as exc:
            return _failure("play_youtube", f"Could not open a browser: {exc}")
        if not opened:
            return _failure("play_youtube", "No web browser is available.")
        return ToolResult(
            tool_name="play_youtube",
            content=spoken,
            success=True,
            metadata={"url": url, "video_id": video_id, "title": title},
        )


# ---------------------------------------------------------------------------
# Media keys, volume and screen lock
# ---------------------------------------------------------------------------

_ACTIONS = (
    "volume_up",
    "volume_down",
    "mute",
    "play_pause",
    "next_track",
    "previous_track",
    "lock_screen",
)
# Windows virtual-key codes for the multimedia keys.
_WINDOWS_MEDIA_KEYS = {
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
    "next_track": 0xB0,
    "previous_track": 0xB1,
    "play_pause": 0xB3,
}
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002


def _press_windows_key(vk_code: int, times: int = 1) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(vk_code, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk_code, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)


def _lock_windows() -> None:
    import ctypes

    if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
        raise OSError("Windows refused to lock the workstation.")


def _run_checked(args: List[str]) -> None:
    proc = _run(args, timeout=10.0)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise OSError(f"{args[0]} failed{': ' + detail if detail else ''}")


def _control_mac(action: str, steps: int) -> None:
    delta = 2 * steps
    scripts = {
        "volume_up": (
            "set volume output volume "
            f"((output volume of (get volume settings)) + {delta})"
        ),
        "volume_down": (
            "set volume output volume "
            f"((output volume of (get volume settings)) - {delta})"
        ),
        "mute": "set volume output muted not (output muted of (get volume settings))",
    }
    if action in scripts:
        _run_checked(["osascript", "-e", scripts[action]])
    elif action == "lock_screen":
        _run_checked(["pmset", "displaysleepnow"])
    else:
        raise NotImplementedError("Media playback keys are not supported on macOS yet.")


def _control_linux(action: str, steps: int) -> None:
    percent = f"{2 * steps}%"
    if action in {"volume_up", "volume_down", "mute"}:
        if shutil.which("pactl"):
            if action == "mute":
                _run_checked(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
            else:
                sign = "+" if action == "volume_up" else "-"
                _run_checked(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{percent}"]
                )
            return
        if shutil.which("amixer"):
            if action == "mute":
                _run_checked(["amixer", "-q", "set", "Master", "toggle"])
            else:
                sign = "+" if action == "volume_up" else "-"
                _run_checked(["amixer", "-q", "set", "Master", f"{percent}{sign}"])
            return
        raise NotImplementedError("Install pactl or amixer to control the volume.")
    if action in {"play_pause", "next_track", "previous_track"}:
        if not shutil.which("playerctl"):
            raise NotImplementedError("Install playerctl to control media playback.")
        command = {
            "play_pause": "play-pause",
            "next_track": "next",
            "previous_track": "previous",
        }[action]
        _run_checked(["playerctl", command])
        return
    if shutil.which("loginctl"):
        _run_checked(["loginctl", "lock-session"])
    elif shutil.which("xdg-screensaver"):
        _run_checked(["xdg-screensaver", "lock"])
    else:
        raise NotImplementedError("No screen locker (loginctl) was found.")


_ACTION_REPLIES = {
    "volume_up": "Volume up.",
    "volume_down": "Volume down.",
    "mute": "Toggled mute.",
    "play_pause": "Toggled play/pause.",
    "next_track": "Skipped to the next track.",
    "previous_track": "Went back to the previous track.",
    "lock_screen": "Locked the screen.",
}


@ToolRegistry.register("system_control")
class SystemControlTool(BaseTool):
    """Press media/volume keys or lock the screen."""

    tool_id = "system_control"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_control",
            description=(
                "Control the computer: change or mute the volume, play/pause, "
                "skip to the next or previous track in the active media player, "
                "or lock the screen."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_ACTIONS),
                        "description": "What to do.",
                    },
                    "steps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "description": (
                            "Volume steps for volume_up/volume_down; one step is "
                            "about 2 percent. Defaults to 5."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            category="desktop",
            latency_estimate=0.2,
            timeout_seconds=15.0,
            required_capabilities=["code:execute"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action")
        if action not in _ACTIONS:
            return _failure(
                "system_control", f"action must be one of: {', '.join(_ACTIONS)}."
            )
        steps = params.get("steps", 5)
        if (
            isinstance(steps, bool)
            or not isinstance(steps, int)
            or not 1 <= steps <= 25
        ):
            return _failure("system_control", "steps must be an integer from 1 to 25.")
        plat = _current_platform()
        try:
            if plat == "win32":
                if action == "lock_screen":
                    _lock_windows()
                else:
                    times = steps if action in {"volume_up", "volume_down"} else 1
                    _press_windows_key(_WINDOWS_MEDIA_KEYS[action], times)
            elif plat == "darwin":
                _control_mac(action, steps)
            else:
                _control_linux(action, steps)
        except NotImplementedError as exc:
            return _failure("system_control", str(exc))
        except (OSError, AttributeError, subprocess.SubprocessError) as exc:
            return _failure(
                "system_control", f"Could not {action.replace('_', ' ')}: {exc}"
            )
        return ToolResult(
            tool_name="system_control",
            content=_ACTION_REPLIES[action],
            success=True,
            metadata={"action": action},
        )


# ---------------------------------------------------------------------------
# System stats and time
# ---------------------------------------------------------------------------


def _gb(value: float) -> float:
    return round(value / (1024**3), 1)


def _spoken_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 60 * 24)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def _system_drive() -> str:
    if _current_platform() == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def collect_system_stats(*, include_processes: bool = False) -> Dict[str, Any]:
    """Return a JSON-serializable snapshot of CPU, memory, disk and battery."""
    import psutil

    cpu_percent = psutil.cpu_percent(interval=0.25)
    memory = psutil.virtual_memory()
    stats: Dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "cpu": {
            "percent": cpu_percent,
            "logical_cores": psutil.cpu_count(logical=True),
        },
        "memory": {
            "used_gb": _gb(memory.total - memory.available),
            "total_gb": _gb(memory.total),
            "percent": memory.percent,
        },
    }
    cores = stats["cpu"]["logical_cores"]
    summary = [
        f"CPU at {cpu_percent:.0f} percent across {cores} cores",
        (
            f"memory {stats['memory']['used_gb']} of {stats['memory']['total_gb']} GB "
            f"used ({memory.percent:.0f} percent)"
        ),
    ]

    drive = _system_drive()
    try:
        disk = psutil.disk_usage(drive)
        stats["disk"] = {
            "path": drive,
            "used_gb": _gb(disk.used),
            "total_gb": _gb(disk.total),
            "percent": disk.percent,
        }
        summary.append(
            f"drive {drive.rstrip(chr(92))} {stats['disk']['used_gb']} of "
            f"{stats['disk']['total_gb']} GB used"
        )
    except OSError:
        pass

    try:
        battery = psutil.sensors_battery()
    except Exception:  # some platforms raise instead of returning None
        battery = None
    if battery is not None:
        info: Dict[str, Any] = {
            "percent": round(battery.percent),
            "plugged_in": bool(battery.power_plugged),
        }
        if isinstance(battery.secsleft, (int, float)) and battery.secsleft > 0:
            info["time_left"] = _spoken_duration(battery.secsleft)
        stats["battery"] = info
        state = "charging" if info["plugged_in"] else "on battery"
        summary.append(f"battery {info['percent']} percent, {state}")

    try:
        uptime_seconds = max(0.0, time.time() - psutil.boot_time())
        stats["uptime"] = _spoken_duration(uptime_seconds)
        summary.append(f"up for {stats['uptime']}")
    except OSError:
        pass

    if include_processes:
        processes = []
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                rss = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
                processes.append((rss, proc.info["name"] or "?"))
            except (psutil.Error, AttributeError, KeyError):
                continue
        processes.sort(reverse=True)
        stats["top_memory_processes"] = [
            {"name": name, "memory_mb": round(rss / (1024**2))}
            for rss, name in processes[:5]
        ]

    stats["summary"] = "; ".join(summary) + "."
    return stats


@ToolRegistry.register("system_stats")
class SystemStatsTool(BaseTool):
    """Report CPU, memory, disk, battery and uptime."""

    tool_id = "system_stats"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_stats",
            description=(
                "Check the computer's health: CPU load, memory, disk space, "
                "battery and uptime. Set include_processes to list the apps "
                "using the most memory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "include_processes": {
                        "type": "boolean",
                        "description": "Include the top memory-using processes.",
                    },
                },
                "additionalProperties": False,
            },
            category="system",
            latency_estimate=0.3,
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        include_processes = params.get("include_processes", False)
        if not isinstance(include_processes, bool):
            return _failure("system_stats", "include_processes must be true or false.")
        try:
            stats = collect_system_stats(include_processes=include_processes)
        except Exception as exc:
            return _failure("system_stats", f"Could not read system stats: {exc}")
        return ToolResult(
            tool_name="system_stats",
            content=json.dumps(stats, ensure_ascii=False, separators=(",", ":")),
            success=True,
            metadata={"summary": stats["summary"]},
        )


def _now() -> datetime:
    """Current local time; wrapped so tests can freeze it."""
    return datetime.now().astimezone()


def spoken_time(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def spoken_date(moment: datetime) -> str:
    return f"{moment:%A}, {moment.day} {moment:%B} {moment.year}"


def format_utc_offset(moment: datetime) -> str:
    """Return *moment*'s UTC offset as ``+HH:MM``."""
    offset = moment.utcoffset() or timedelta(0)
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


@ToolRegistry.register("current_datetime")
class CurrentDateTimeTool(BaseTool):
    """Tell the current date and time, locally or in another time zone."""

    tool_id = "current_datetime"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="current_datetime",
            description=(
                "Get the current date and time. Leave timezone empty for the "
                "user's local time, or pass an IANA zone such as 'Asia/Tokyo', "
                "'Europe/London' or 'America/New_York'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA time zone name.",
                    },
                },
                "additionalProperties": False,
            },
            category="system",
            latency_estimate=0.0,
            timeout_seconds=5.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        zone_name = params.get("timezone") or ""
        moment = _now()
        if zone_name:
            try:
                zone_name = _validate_text(zone_name, field="timezone", max_length=64)
                if zone_name.upper() in {"UTC", "GMT", "Z"}:
                    moment = moment.astimezone(timezone.utc)
                else:
                    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

                    try:
                        moment = moment.astimezone(ZoneInfo(zone_name))
                    except (ZoneInfoNotFoundError, ValueError):
                        return _failure(
                            "current_datetime",
                            f"Unknown time zone '{zone_name}'. Use an IANA name "
                            "such as 'Asia/Karachi' (on Windows this needs the "
                            "tzdata package).",
                        )
            except ValueError as exc:
                return _failure("current_datetime", str(exc))
        label = zone_name or moment.tzname() or "local time"
        payload = {
            "summary": (
                f"It is {spoken_time(moment)} on {spoken_date(moment)} ({label})."
            ),
            "iso": moment.isoformat(timespec="seconds"),
            "date": spoken_date(moment),
            "time": spoken_time(moment),
            "time_24h": f"{moment:%H:%M}",
            "timezone": label,
            "utc_offset": format_utc_offset(moment),
        }
        return ToolResult(
            tool_name="current_datetime",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            success=True,
        )


__all__ = [
    "AppNotFoundError",
    "CurrentDateTimeTool",
    "OpenAppTool",
    "OpenWebsiteTool",
    "PlayYouTubeTool",
    "SystemControlTool",
    "SystemStatsTool",
    "collect_system_stats",
    "find_youtube_video",
    "format_utc_offset",
    "normalize_url",
    "open_app_by_name",
    "spoken_date",
    "spoken_time",
]
