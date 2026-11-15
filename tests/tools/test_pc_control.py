"""Tests for the JARVIS-mode desktop-control tools (tools/pc_control.py).

Every operating-system side effect is replaced by a recording fake, so these
tests never launch apps, open browsers, or press keys.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolCall
from openjarvis.security.capabilities import (
    DEFAULT_TOOL_CAPABILITIES,
    canonical_tool_capabilities,
)
from openjarvis.security.taint import TaintLabel, TaintSet, check_taint
from openjarvis.tools import pc_control
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.pc_control import (
    CurrentDateTimeTool,
    OpenAppTool,
    OpenWebsiteTool,
    PlayYouTubeTool,
    SystemControlTool,
    SystemStatsTool,
    normalize_url,
)

TOOL_NAMES = (
    "open_app",
    "open_website",
    "play_youtube",
    "system_control",
    "system_stats",
    "current_datetime",
)


class _Recorder:
    """Collects calls made through a patched platform seam."""

    def __init__(self, side_effect: Exception | None = None) -> None:
        self.calls: list = []
        self._side_effect = side_effect

    def __call__(self, *args):
        self.calls.append(args[0] if len(args) == 1 else args)
        if self._side_effect is not None:
            raise self._side_effect


@pytest.fixture
def windows(monkeypatch):
    """Simulate Windows with recording launch seams and no Start-menu apps."""
    started = _Recorder()
    spawned = _Recorder()
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "win32")
    monkeypatch.setattr(pc_control, "_startfile", started)
    monkeypatch.setattr(pc_control, "_popen", spawned)
    monkeypatch.setattr(pc_control, "_windows_start_apps", lambda: [])
    monkeypatch.setattr(pc_control, "_windows_start_menu_shortcuts", lambda: [])
    return started, spawned


@pytest.fixture
def browser(monkeypatch):
    opened: list[str] = []

    def _open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(pc_control, "_open_url", _open)
    return opened


# ---------------------------------------------------------------------------
# Registration and security inventory
# ---------------------------------------------------------------------------


def test_all_desktop_tools_register():
    importlib.reload(pc_control)
    for name in TOOL_NAMES:
        assert ToolRegistry.contains(name), name


def test_capability_floor_matches_the_side_effects():
    tools = {
        "open_app": OpenAppTool(),
        "open_website": OpenWebsiteTool(),
        "play_youtube": PlayYouTubeTool(),
        "system_control": SystemControlTool(),
        "system_stats": SystemStatsTool(),
        "current_datetime": CurrentDateTimeTool(),
    }
    for name, tool in tools.items():
        assert tool.spec.name == name
        assert name in DEFAULT_TOOL_CAPABILITIES
        declared = set(tool.spec.required_capabilities)
        canonical = {getattr(c, "value", c) for c in canonical_tool_capabilities(tool)}
        # The non-bypassable floor and the advertised spec must agree.
        assert canonical == declared

    assert "code:execute" in tools["open_app"].spec.required_capabilities
    assert canonical_tool_capabilities(tools["system_stats"]) == []
    assert canonical_tool_capabilities(tools["current_datetime"]) == []


def test_open_website_is_an_exfiltration_sink():
    assert check_taint("open_website", TaintSet.from_labels(TaintLabel.SECRET))
    assert check_taint("open_website", TaintSet.from_labels(TaintLabel.PII))
    assert check_taint("open_website", TaintSet()) is None
    assert OpenWebsiteTool.is_local is False
    assert PlayYouTubeTool.is_local is False


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------


def test_best_match_prefers_exact_then_shorter_names():
    entries = [("Word 2016", 1), ("Word", 2), ("WordPad", 3)]
    match = pc_control._best_match("word", entries)
    assert match is not None and match.value == 2


def test_best_match_skips_uninstallers_and_help_links():
    entries = [("Uninstall Foo", "bad"), ("Foo Help", "bad"), ("Foo Studio", "ok")]
    match = pc_control._best_match("foo", entries)
    assert match is not None and match.value == "ok"


def test_best_match_tolerates_small_misspellings_but_not_strangers():
    entries = [("Spotify", "spotify-id")]
    assert pc_control._best_match("spotfy", entries).value == "spotify-id"
    assert pc_control._best_match("calculator", entries) is None


def test_normalize_name_drops_filler_and_punctuation():
    assert pc_control._normalize_name("  The Spotify App! ") == "spotify"
    assert pc_control._normalize_name("Notepad++") == "notepad++"


# ---------------------------------------------------------------------------
# open_app
# ---------------------------------------------------------------------------


def test_open_app_launches_builtin_windows_app(windows):
    started, _ = windows
    result = OpenAppTool(apps={}).execute(name="Calculator")
    assert result.success, result.content
    assert started.calls == ["calc.exe"]
    assert result.content == "Opened Calculator."


def test_open_app_resolves_aliases(windows):
    started, _ = windows
    assert OpenAppTool(apps={}).execute(name="explorer").success
    assert OpenAppTool(apps={}).execute(name="google chrome").success
    assert started.calls == ["explorer.exe", "chrome.exe"]


def test_open_app_falls_back_to_start_menu_when_app_paths_entry_is_missing(
    windows, monkeypatch
):
    _, spawned = windows
    monkeypatch.setattr(
        pc_control, "_startfile", _Recorder(FileNotFoundError("chrome.exe"))
    )
    monkeypatch.setattr(
        pc_control, "_windows_start_apps", lambda: [("Google Chrome", "Chrome")]
    )
    result = OpenAppTool(apps={}).execute(name="chrome")
    assert result.success, result.content
    assert spawned.calls == [["explorer.exe", "shell:AppsFolder\\Chrome"]]


def test_open_app_finds_store_apps_through_get_startapps(windows, monkeypatch):
    _, spawned = windows
    app_id = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
    monkeypatch.setattr(
        pc_control,
        "_windows_start_apps",
        lambda: [("Uninstall Spotify", "nope"), ("Spotify", app_id)],
    )
    result = OpenAppTool(apps={}).execute(name="spotify")
    assert result.success, result.content
    assert spawned.calls == [["explorer.exe", f"shell:AppsFolder\\{app_id}"]]
    assert result.metadata["app"] == "Spotify"


def test_open_app_rewrites_vs_code_for_the_start_menu_search(windows, monkeypatch):
    _, spawned = windows
    monkeypatch.setattr(
        pc_control,
        "_windows_start_apps",
        lambda: [("Visual Studio Code", "Microsoft.VisualStudioCode")],
    )
    assert OpenAppTool(apps={}).execute(name="VS Code").success
    assert spawned.calls == [
        ["explorer.exe", "shell:AppsFolder\\Microsoft.VisualStudioCode"]
    ]


def test_open_app_uses_start_menu_shortcuts_as_last_resort(windows, monkeypatch):
    started, _ = windows
    shortcut = Path("C:/ProgramData/Start Menu/Programs/Steam/Steam.lnk")
    monkeypatch.setattr(
        pc_control, "_windows_start_menu_shortcuts", lambda: [("Steam", shortcut)]
    )
    assert OpenAppTool(apps={}).execute(name="steam").success
    assert started.calls == [str(shortcut)]


def test_open_app_prefers_user_configured_apps(windows):
    started, _ = windows
    tool = OpenAppTool(apps={"Minecraft": "C:\\Games\\Minecraft.exe"})
    result = tool.execute(name="minecraft")
    assert result.success
    assert started.calls == ["C:\\Games\\Minecraft.exe"]


def test_open_app_reports_unknown_apps(windows):
    result = OpenAppTool(apps={}).execute(name="Totally Unknown Thing")
    assert not result.success
    assert "couldn't find an app" in result.content
    assert "[jarvis.apps]" in result.content


@pytest.mark.parametrize(
    "name",
    ["C:\\Windows\\System32\\cmd.exe", "/usr/bin/xterm", "..\\evil.bat"],
)
def test_open_app_never_accepts_paths_from_the_model(windows, name):
    started, spawned = windows
    result = OpenAppTool(apps={}).execute(name=name)
    assert not result.success
    assert started.calls == [] and spawned.calls == []


@pytest.mark.parametrize("name", ["", "   ", 42, None, "x" * 101, "note\x00pad"])
def test_open_app_validates_the_name(windows, name):
    assert not OpenAppTool(apps={}).execute(name=name).success


def test_open_app_points_urls_at_open_website(windows):
    result = OpenAppTool(apps={}).execute(name="https://youtube.com")
    assert not result.success and "open_website" in result.content


def test_open_app_reports_launch_errors(windows, monkeypatch):
    monkeypatch.setattr(
        pc_control, "_startfile", _Recorder(PermissionError("access denied"))
    )
    result = OpenAppTool(apps={"Locked": "C:\\locked.exe"}).execute(name="locked")
    assert not result.success and "access denied" in result.content


def test_windows_start_apps_parses_powershell_json(monkeypatch):
    monkeypatch.setattr(pc_control, "_start_apps_cache", None)
    payload = [{"Name": "Calculator", "AppID": "Microsoft.Calc!App"}, {"Name": 3}]
    calls = []

    def _fake_run(args, *, timeout):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(pc_control, "_run", _fake_run)
    assert pc_control._windows_start_apps() == [("Calculator", "Microsoft.Calc!App")]
    # Cached: PowerShell runs once per TTL window.
    assert pc_control._windows_start_apps() == [("Calculator", "Microsoft.Calc!App")]
    assert len(calls) == 1
    assert calls[0][0] == "powershell.exe" and "Get-StartApps" in calls[0][-1]


def test_windows_start_apps_accepts_a_single_object_and_survives_errors(monkeypatch):
    monkeypatch.setattr(pc_control, "_start_apps_cache", None)
    monkeypatch.setattr(
        pc_control,
        "_run",
        lambda args, *, timeout: subprocess.CompletedProcess(
            args, 0, json.dumps({"Name": "Paint", "AppID": "Paint!App"}), ""
        ),
    )
    assert pc_control._windows_start_apps() == [("Paint", "Paint!App")]

    monkeypatch.setattr(pc_control, "_start_apps_cache", None)

    def _boom(args, *, timeout):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(pc_control, "_run", _boom)
    assert pc_control._windows_start_apps() == []


def test_open_app_on_macos_uses_open_dash_a(monkeypatch):
    spawned = _Recorder()
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(pc_control, "_popen", spawned)
    safari = Path("/Applications/Safari.app")
    monkeypatch.setattr(pc_control, "_mac_applications", lambda: [("Safari", safari)])
    assert OpenAppTool(apps={}).execute(name="browser").success
    assert spawned.calls == [["open", "-a", str(safari)]]


def test_open_app_on_linux_reads_desktop_entries(monkeypatch, tmp_path):
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    (apps_dir / "org.gnome.Calculator.desktop").write_text(
        "[Desktop Entry]\nName=Calculator\nExec=gnome-calculator\n", encoding="utf-8"
    )
    (apps_dir / "hidden.desktop").write_text(
        "[Desktop Entry]\nName=Hidden Calculator Helper\nNoDisplay=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
    entries = pc_control._linux_desktop_entries()
    assert ("Calculator", apps_dir / "org.gnome.Calculator.desktop") in entries
    assert all(name != "Hidden Calculator Helper" for name, _ in entries)

    spawned = _Recorder()
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "linux")
    monkeypatch.setattr(pc_control, "_popen", spawned)
    monkeypatch.setattr(
        pc_control.shutil,
        "which",
        lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None,
    )
    assert OpenAppTool(apps={}).execute(name="calc").success
    assert spawned.calls == [["gtk-launch", "org.gnome.Calculator.desktop"]]


# ---------------------------------------------------------------------------
# open_website
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("youtube", "https://www.youtube.com"),
        ("The Gmail", "https://mail.google.com"),
        ("bbc.co.uk", "https://bbc.co.uk"),
        ("HTTPS://Example.com/a?b=1#c", "https://Example.com/a?b=1#c"),
        ("http://example.com", "http://example.com"),
        ("localhost:3000", "https://localhost:3000"),
        ("example.com:8080/path", "https://example.com:8080/path"),
    ],
)
def test_normalize_url_accepts_web_addresses(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "file:///C:/Windows/win.ini",
        "ftp://example.com",
        "data:text/html,hi",
        "https://user:pass@example.com",
        "not a website",
        "somewhere",
        "https://",
        "x" * 3000,
    ],
)
def test_normalize_url_rejects_everything_else(raw):
    with pytest.raises(ValueError):
        normalize_url(raw)


def test_open_website_opens_the_normalized_url(browser):
    result = OpenWebsiteTool().execute(target="github")
    assert result.success
    assert browser == ["https://github.com"]
    assert result.metadata["url"] == "https://github.com"


def test_open_website_searches_google(browser):
    result = OpenWebsiteTool().execute(search_query="weather in Rawalpindi & more")
    assert result.success
    assert browser == ["https://www.google.com/search?q=weather+in+Rawalpindi+%26+more"]


def test_open_website_requires_a_target(browser):
    assert not OpenWebsiteTool().execute().success
    assert not OpenWebsiteTool().execute(target="javascript:alert(1)").success
    assert browser == []


def test_open_website_reports_a_missing_browser(monkeypatch):
    monkeypatch.setattr(pc_control, "_open_url", lambda url: False)
    result = OpenWebsiteTool().execute(target="youtube")
    assert not result.success and "browser" in result.content


# ---------------------------------------------------------------------------
# play_youtube
# ---------------------------------------------------------------------------

_RESULTS_HTML = (
    '<script>var ytInitialData = {"contents":[{"adSlotRenderer":{}},'
    '{"videoRenderer":{"videoId":"dQw4w9WgXcQ","thumbnail":{},'
    '"title":{"runs":[{"text":"Never Gonna Give You Up \\u0026 More"}]}}}]}'
    "</script>"
)


def _fake_get(html: str, status: int = 200, captured: list | None = None):
    def _get(url, **kwargs):
        if captured is not None:
            captured.append((url, kwargs))
        return httpx.Response(status, text=html, request=httpx.Request("GET", url))

    return _get


def test_find_youtube_video_parses_the_first_organic_result(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        pc_control.httpx, "get", _fake_get(_RESULTS_HTML, captured=captured)
    )
    assert pc_control.find_youtube_video("rick astley") == (
        "dQw4w9WgXcQ",
        "Never Gonna Give You Up & More",
    )
    url, kwargs = captured[0]
    assert url == "https://www.youtube.com/results"
    assert kwargs["params"]["search_query"] == "rick astley"


def test_play_youtube_opens_the_watch_page(monkeypatch, browser):
    monkeypatch.setattr(pc_control.httpx, "get", _fake_get(_RESULTS_HTML))
    result = PlayYouTubeTool().execute(query="rick astley")
    assert result.success
    assert browser == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    assert "Never Gonna Give You Up" in result.content


def test_play_youtube_falls_back_to_search_results(monkeypatch, browser):
    monkeypatch.setattr(pc_control.httpx, "get", _fake_get("busy", status=503))
    result = PlayYouTubeTool().execute(query="lofi beats")
    assert result.success
    assert browser == ["https://www.youtube.com/results?search_query=lofi+beats"]


def test_play_youtube_without_autoplay_skips_the_network(monkeypatch, browser):
    def _no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(pc_control.httpx, "get", _no_network)
    result = PlayYouTubeTool().execute(query="cats", autoplay=False)
    assert result.success
    assert browser == ["https://www.youtube.com/results?search_query=cats"]


@pytest.mark.parametrize(
    "params", [{}, {"query": ""}, {"query": "x", "autoplay": "yes"}]
)
def test_play_youtube_validates_input(browser, params):
    assert not PlayYouTubeTool().execute(**params).success
    assert browser == []


# ---------------------------------------------------------------------------
# system_control
# ---------------------------------------------------------------------------


def test_system_control_presses_windows_media_keys(monkeypatch):
    pressed: list = []
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "win32")
    monkeypatch.setattr(
        pc_control,
        "_press_windows_key",
        lambda vk, times=1: pressed.append((vk, times)),
    )
    tool = SystemControlTool()
    assert tool.execute(action="volume_up", steps=3).success
    assert tool.execute(action="mute").success
    assert tool.execute(action="play_pause", steps=9).success
    assert pressed == [(0xAF, 3), (0xAD, 1), (0xB3, 1)]


def test_system_control_locks_windows(monkeypatch):
    locked = []
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "win32")
    monkeypatch.setattr(pc_control, "_lock_windows", lambda: locked.append(True))
    result = SystemControlTool().execute(action="lock_screen")
    assert result.success and locked == [True]


def test_system_control_uses_pactl_on_linux(monkeypatch):
    commands = []
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        pc_control.shutil,
        "which",
        lambda cmd: f"/usr/bin/{cmd}" if cmd == "pactl" else None,
    )
    monkeypatch.setattr(
        pc_control,
        "_run",
        lambda args, *, timeout: (
            commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    assert SystemControlTool().execute(action="volume_down", steps=2).success
    assert commands == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-4%"]]


def test_system_control_explains_missing_linux_helpers(monkeypatch):
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "linux")
    monkeypatch.setattr(pc_control.shutil, "which", lambda cmd: None)
    result = SystemControlTool().execute(action="next_track")
    assert not result.success and "playerctl" in result.content


def test_system_control_uses_osascript_on_macos(monkeypatch):
    commands = []
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(
        pc_control,
        "_run",
        lambda args, *, timeout: (
            commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    assert SystemControlTool().execute(action="volume_up").success
    assert commands[0][0] == "osascript" and "+ 10" in commands[0][2]
    assert not SystemControlTool().execute(action="next_track").success


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"action": "shutdown"},
        {"action": "volume_up", "steps": 0},
        {"action": "volume_up", "steps": 26},
        {"action": "volume_up", "steps": True},
    ],
)
def test_system_control_validates_input(monkeypatch, params):
    monkeypatch.setattr(pc_control, "_current_platform", lambda: "win32")
    monkeypatch.setattr(
        pc_control, "_press_windows_key", lambda *a, **k: pytest.fail("pressed")
    )
    assert not SystemControlTool().execute(**params).success


# ---------------------------------------------------------------------------
# system_stats and current_datetime
# ---------------------------------------------------------------------------


def test_system_stats_reports_cpu_memory_and_a_summary():
    result = SystemStatsTool().execute()
    assert result.success, result.content
    stats = json.loads(result.content)
    assert {"cpu", "memory", "summary"} <= set(stats)
    assert 0 <= stats["memory"]["percent"] <= 100
    assert stats["summary"].startswith("CPU at")
    assert "top_memory_processes" not in stats


def test_system_stats_can_list_heavy_processes():
    stats = json.loads(SystemStatsTool().execute(include_processes=True).content)
    assert isinstance(stats["top_memory_processes"], list)
    assert len(stats["top_memory_processes"]) <= 5


def test_system_stats_validates_input():
    assert not SystemStatsTool().execute(include_processes="yes").success


_FROZEN = datetime(2026, 11, 15, 14, 5, 9, tzinfo=timezone(timedelta(hours=5), "PKT"))


def test_current_datetime_speaks_local_time(monkeypatch):
    monkeypatch.setattr(pc_control, "_now", lambda: _FROZEN)
    payload = json.loads(CurrentDateTimeTool().execute().content)
    assert payload["summary"] == "It is 2:05 PM on Sunday, 15 November 2026 (PKT)."
    assert payload["time_24h"] == "14:05"
    assert payload["utc_offset"] == "+05:00"
    assert payload["iso"] == "2026-11-15T14:05:09+05:00"


def test_current_datetime_converts_time_zones(monkeypatch):
    monkeypatch.setattr(pc_control, "_now", lambda: _FROZEN)
    tool = CurrentDateTimeTool()
    utc = json.loads(tool.execute(timezone="UTC").content)
    assert utc["time"] == "9:05 AM" and utc["utc_offset"] == "+00:00"
    try:
        tokyo = json.loads(tool.execute(timezone="Asia/Tokyo").content)
    except json.JSONDecodeError:  # pragma: no cover - no tz database installed
        pytest.skip("IANA time zone database unavailable")
    assert tokyo["time"] == "6:05 PM" and tokyo["utc_offset"] == "+09:00"


def test_current_datetime_rejects_unknown_zones(monkeypatch):
    monkeypatch.setattr(pc_control, "_now", lambda: _FROZEN)
    result = CurrentDateTimeTool().execute(timezone="Mars/Olympus_Mons")
    assert not result.success and "Unknown time zone" in result.content


def test_tools_run_through_the_executor(monkeypatch):
    monkeypatch.setattr(pc_control, "_now", lambda: _FROZEN)
    executor = ToolExecutor([CurrentDateTimeTool(), SystemStatsTool()])
    result = executor.execute(ToolCall(id="1", name="current_datetime", arguments="{}"))
    assert result.success
    assert "2:05 PM" in result.content
