"""Server errors must reach browser clients with CORS headers.

The desktop app's webview (origin ``http://tauri.localhost``) calls the API
cross-origin. Starlette sends the 500 for an unhandled exception from outside
CORSMiddleware, so that reply had no ``Access-Control-Allow-Origin`` header:
the browser hid it, ``fetch`` rejected with "Failed to fetch", and the JARVIS
HUD could only ask whether the server was running, although the server was up
and Ollama had said exactly what was wrong (too little free memory).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.engine._base import EngineConnectionError  # noqa: E402
from openjarvis.server.app import create_app  # noqa: E402

TAURI_ORIGIN = "http://tauri.localhost"
LOW_MEMORY = (
    "Ollama returned 500: model requires more system memory (3.1 GiB) "
    "than is available (2.4 GiB)"
)


def _make_engine():
    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["qwen3.5:0.8b"]
    return engine


def _make_failing_agent(error: Exception):
    agent = MagicMock()
    agent.agent_id = "jarvis"
    agent.run.side_effect = error
    return agent


def _test_config():
    from openjarvis.core.config import JarvisConfig

    cfg = JarvisConfig()
    cfg.analytics.enabled = False
    cfg.traces.enabled = False
    return cfg


def _ask(client: TestClient):
    """The JARVIS HUD's request: one non-streaming turn, cross-origin."""
    return client.post(
        "/v1/chat/completions",
        headers={"Origin": TAURI_ORIGIN},
        json={
            "model": "qwen3.5:0.8b",
            "messages": [{"role": "user", "content": "hey"}],
            "stream": False,
        },
    )


class TestFailedCompletionsAreReadable:
    def test_agent_failure_is_json_with_cors_headers(self):
        agent = _make_failing_agent(RuntimeError(LOW_MEMORY))
        app = create_app(
            _make_engine(), "qwen3.5:0.8b", agent=agent, config=_test_config()
        )

        resp = _ask(TestClient(app))

        assert resp.status_code == 500
        assert resp.headers.get("access-control-allow-origin") == TAURI_ORIGIN
        assert resp.json() == {"detail": LOW_MEMORY}

    def test_unreachable_engine_is_a_503(self):
        agent = _make_failing_agent(
            EngineConnectionError("Ollama not reachable at http://localhost:11434")
        )
        app = create_app(
            _make_engine(), "qwen3.5:0.8b", agent=agent, config=_test_config()
        )

        resp = _ask(TestClient(app))

        assert resp.status_code == 503
        assert resp.headers.get("access-control-allow-origin") == TAURI_ORIGIN
        assert "Ollama not reachable" in resp.json()["detail"]

    def test_direct_engine_failure_is_json_with_cors_headers(self):
        engine = _make_engine()
        engine.generate.side_effect = RuntimeError(LOW_MEMORY)
        app = create_app(engine, "qwen3.5:0.8b", config=_test_config())

        resp = _ask(TestClient(app))

        assert resp.status_code == 500
        assert resp.headers.get("access-control-allow-origin") == TAURI_ORIGIN
        assert resp.json() == {"detail": LOW_MEMORY}

    def test_failure_is_logged_with_the_traceback(self, caplog):
        agent = _make_failing_agent(RuntimeError(LOW_MEMORY))
        app = create_app(
            _make_engine(), "qwen3.5:0.8b", agent=agent, config=_test_config()
        )

        with caplog.at_level("ERROR", logger="openjarvis.server"):
            _ask(TestClient(app))

        records = [r for r in caplog.records if "Chat completion failed" in r.message]
        assert records and records[0].exc_info is not None


class TestUnhandledExceptions:
    @staticmethod
    def _app_with_broken_route():
        app = create_app(_make_engine(), "qwen3.5:0.8b", config=_test_config())

        @app.get("/v1/test-broken")
        async def broken():
            raise ValueError("boom")

        # With a built web UI, create_app registers a catch-all route for it;
        # match this route first.
        app.router.routes.insert(0, app.router.routes.pop())
        return app

    def test_json_500_with_cors_headers(self):
        client = TestClient(
            self._app_with_broken_route(), raise_server_exceptions=False
        )

        resp = client.get("/v1/test-broken", headers={"Origin": TAURI_ORIGIN})

        assert resp.status_code == 500
        assert resp.headers.get("access-control-allow-origin") == TAURI_ORIGIN
        assert "ValueError" in resp.json()["detail"]
        # The exception text stays in the server log, not in the reply.
        assert "boom" not in resp.text

    def test_exception_still_propagates_to_the_server(self):
        client = TestClient(self._app_with_broken_route())

        with pytest.raises(ValueError, match="boom"):
            client.get("/v1/test-broken", headers={"Origin": TAURI_ORIGIN})

    def test_disallowed_origin_gets_no_cors_header(self):
        client = TestClient(
            self._app_with_broken_route(), raise_server_exceptions=False
        )

        resp = client.get("/v1/test-broken", headers={"Origin": "https://evil.example"})

        assert resp.status_code == 500
        assert "access-control-allow-origin" not in resp.headers
