"""Tests for the JARVIS HUD endpoint (server/jarvis_routes.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.core.config import JarvisConfig  # noqa: E402
from openjarvis.server.jarvis_routes import jarvis_router  # noqa: E402

_STATS = {
    "os": "Windows 11",
    "cpu": {"percent": 12.0, "logical_cores": 8},
    "memory": {"used_gb": 6.1, "total_gb": 16.0, "percent": 38.0},
    "summary": "CPU at 12 percent across 8 cores.",
}


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(spec=SimpleNamespace(name=name))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.tools.pc_control.collect_system_stats", lambda: dict(_STATS)
    )
    config = JarvisConfig()
    config.jarvis.honorific = "boss"
    app = FastAPI()
    app.state.config = config
    app.state.model = "gemini-3.5-flash-lite"
    app.state.engine_name = "multi"
    app.state.agent = SimpleNamespace(
        agent_id="jarvis", _tools=[_tool("open_app"), _tool("system_stats")]
    )
    app.include_router(jarvis_router)
    return app


def test_status_reports_brain_settings_and_stats(app):
    data = TestClient(app).get("/v1/jarvis/status").json()
    assert data["agent"] == "jarvis"
    assert data["jarvis_mode"] is True
    assert data["model"] == "gemini-3.5-flash-lite"
    assert data["engine"] == "multi"
    assert data["tools"] == ["open_app", "system_stats"]
    assert data["wake_word"] == "jarvis"
    assert data["honorific"] == "boss"
    assert data["follow_up_seconds"] == 8.0
    assert data["require_wake_word"] is True
    assert data["voice"] == "kokoro / bm_george"
    assert data["stats"]["cpu"]["percent"] == 12.0


def test_other_agents_are_not_jarvis_mode(app):
    app.state.agent = None
    app.state.agent_name = "orchestrator"
    data = TestClient(app).get("/v1/jarvis/status").json()
    assert data["agent"] == "orchestrator"
    assert data["jarvis_mode"] is False
    assert data["tools"] == []


def test_stats_failure_is_reported_not_raised(app, monkeypatch):
    def broken():
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr("openjarvis.tools.pc_control.collect_system_stats", broken)
    response = TestClient(app).get("/v1/jarvis/status")
    assert response.status_code == 200
    assert response.json()["stats"] == {"error": "psutil exploded"}


def test_router_is_part_of_the_app():
    from openjarvis.server.api_routes import include_all_routes

    app = FastAPI()
    include_all_routes(app)
    assert any(getattr(r, "path", "") == "/v1/jarvis/status" for r in app.routes)
