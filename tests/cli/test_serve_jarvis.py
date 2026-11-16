"""``jarvis serve --agent jarvis``: brain auto-selection and default toolkit."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from openjarvis.cli import cli

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

serve_mod = importlib.import_module("openjarvis.cli.serve")


def _fake_engine() -> MagicMock:
    engine = MagicMock()
    engine.list_models.return_value = ["gemini-3.5-flash-lite"]
    engine.health.return_value = True
    engine.engine_id = "cloud"
    return engine


def _register_jarvis_and_tools() -> None:
    """conftest clears the registries; re-run the registration decorators."""
    import openjarvis.agents.jarvis as jarvis_module

    importlib.reload(jarvis_module)
    for module in (
        "openjarvis.tools.pc_control",
        "openjarvis.tools.weather",
        "openjarvis.tools.web_search",
        "openjarvis.tools.calculator",
        "openjarvis.tools.memory_manage",
        "openjarvis.tools.user_profile_manage",
        "openjarvis.tools.think",
    ):
        importlib.reload(importlib.import_module(module))


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Boot serve far enough to capture the agent and the chosen model."""
    from openjarvis.cli import _jarvis_brain
    from openjarvis.core.config import JarvisConfig

    _register_jarvis_and_tools()
    soul = tmp_path / "SOUL.md"
    soul.write_text("SERVE_JARVIS_SENTINEL", encoding="utf-8")

    config = JarvisConfig()
    config.server.port = 8124
    config.memory_files.soul_path = str(soul)
    config.telemetry.enabled = False
    config.agent_manager.enabled = False
    config.sessions.enabled = False
    config.channel.enabled = False
    config.skills.enabled = False
    config.agent.context_from_memory = False

    engine = _fake_engine()
    state: dict = {
        "brain_calls": [],
        "brains": [],
        "engine_models": [],
        "engine_keys": [],
    }

    def fake_select_brain(cfg, *, brain=None, model=None, engine_key=None):
        state["brain_calls"].append(engine_key)
        state["brains"].append(brain)
        return _jarvis_brain.BrainChoice(
            "gemini", "Google Gemini", "cloud", engine, "gemini-3.5-flash-lite"
        )

    def fake_get_engine(cfg, engine_key=None, model=None):
        state["engine_models"].append(model)
        state["engine_keys"].append(engine_key)
        return ("cloud", engine)

    monkeypatch.setattr(_jarvis_brain, "select_brain", fake_select_brain)
    monkeypatch.setattr(_jarvis_brain, "load_saved_cloud_keys", lambda: [])
    monkeypatch.setattr(serve_mod, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(serve_mod, "get_engine", fake_get_engine)
    monkeypatch.setattr(serve_mod, "discover_engines", lambda *a, **k: {})
    monkeypatch.setattr(serve_mod, "discover_models", lambda *a, **k: {})
    sec = MagicMock()
    sec.engine = engine
    sec.capability_policy = None
    sec.rate_limiter = None
    sec.audit_logger = None
    monkeypatch.setattr("openjarvis.security.setup_security", lambda *a, **k: sec)

    def run(*args: str):
        captured: dict = {}

        def capture(*a, **kwargs):
            captured.update(kwargs)
            return MagicMock(name="app")

        with (
            patch("openjarvis.server.app.create_app", side_effect=capture),
            patch("openjarvis.server.daemon.run_server", lambda *a, **k: None),
        ):
            result = CliRunner().invoke(cli, ["serve", *args], catch_exceptions=False)
        return result, captured

    state["run"] = run
    state["config"] = config
    return state


def test_jarvis_agent_gets_brain_and_default_tools(served):
    from openjarvis.agents.jarvis import DEFAULT_JARVIS_TOOLS, JarvisAgent

    result, app_kwargs = served["run"]("--agent", "jarvis")

    assert result.exit_code == 0, result.output
    assert "Google Gemini · gemini-3.5-flash-lite" in result.output
    assert served["brain_calls"] == [None]
    assert served["engine_models"][0] == "gemini-3.5-flash-lite"
    agent = app_kwargs["agent"]
    assert isinstance(agent, JarvisAgent)
    assert agent._model == "gemini-3.5-flash-lite"
    assert {t.spec.name for t in agent._tools} == set(DEFAULT_JARVIS_TOOLS)
    prompt = agent.compose_system_prompt()
    assert "You are JARVIS" in prompt
    assert "SERVE_JARVIS_SENTINEL" in prompt


def test_brain_option_reaches_brain_selection(served):
    # The desktop app's cloud source: never Ollama, the provider from setup.
    result, app_kwargs = served["run"](
        "--agent", "jarvis", "--engine", "cloud", "--brain", "gemini"
    )

    assert result.exit_code == 0, result.output
    assert served["brains"] == ["gemini"]
    assert served["brain_calls"] == ["cloud"]
    assert served["engine_keys"][0] == "cloud"
    assert served["engine_models"][0] == "gemini-3.5-flash-lite"
    assert app_kwargs["agent"]._model == "gemini-3.5-flash-lite"


def test_brain_defaults_to_auto_selection(served):
    result, _ = served["run"]("--agent", "jarvis")
    assert result.exit_code == 0, result.output
    assert served["brains"] == [None]


def test_unknown_brain_is_rejected(served):
    result, app_kwargs = served["run"]("--agent", "jarvis", "--brain", "skynet")
    assert result.exit_code == 2
    assert "skynet" in result.output
    assert served["brain_calls"] == []
    assert "agent" not in app_kwargs


def test_agent_from_server_config(served):
    served["config"].server.agent = "jarvis"
    result, app_kwargs = served["run"]()
    assert result.exit_code == 0, result.output
    assert served["brain_calls"] == [None]


def test_explicit_model_skips_brain_selection(served):
    result, app_kwargs = served["run"]("--agent", "jarvis", "--model", "gpt-6-luna")
    assert result.exit_code == 0, result.output
    assert served["brain_calls"] == []
    assert app_kwargs["agent"]._model == "gpt-6-luna"


def test_other_agents_are_unaffected(served):
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.registry import AgentRegistry

    if not AgentRegistry.contains("orchestrator"):
        AgentRegistry.register_value("orchestrator", OrchestratorAgent)
    served["config"].intelligence.default_model = "gemini-3.5-flash-lite"
    result, _ = served["run"]("--agent", "orchestrator")
    assert result.exit_code == 0, result.output
    assert served["brain_calls"] == []


def test_no_brain_exits_with_setup_help(served, monkeypatch):
    from openjarvis.cli import _jarvis_brain

    def unavailable(*a, **k):
        raise _jarvis_brain.BrainUnavailableError("Get a free key at aistudio")

    monkeypatch.setattr(_jarvis_brain, "select_brain", unavailable)
    result, app_kwargs = served["run"]("--agent", "jarvis")
    assert result.exit_code == 1
    assert "Get a free key at aistudio" in result.output
    assert "agent" not in app_kwargs
