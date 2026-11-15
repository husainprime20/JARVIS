"""Tests for JARVIS brain selection (cli/_jarvis_brain.py)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from openjarvis.cli import _jarvis_brain as brain_mod
from openjarvis.cli._jarvis_brain import (
    BRAINS,
    BrainUnavailableError,
    detect_brains,
    load_saved_cloud_keys,
    pick_local_model,
    select_brain,
    setup_help,
)
from openjarvis.core.config import JarvisConfig

_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


class _FakeCloud:
    """Serves models whose provider SDK is "installed"."""

    def __init__(self, providers=("gemini", "gpt", "claude", "openrouter")):
        self._providers = providers

    def health(self):
        return True

    def can_serve(self, model):
        return any(p in model for p in self._providers)

    def list_models(self):
        return []


class _FakeOllama:
    def __init__(self, models=(), healthy=True):
        self._models = list(models)
        self._healthy = healthy

    def health(self):
        return self._healthy

    def can_serve(self, model):
        return model in self._models

    def list_models(self):
        return list(self._models)


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(brain_mod, "recommended_local_model", lambda: "qwen3.5:4b")


@pytest.fixture
def engines(monkeypatch):
    """Control what ``_make`` returns per engine key."""
    available: dict = {"cloud": _FakeCloud(), "ollama": _FakeOllama(healthy=False)}
    monkeypatch.setattr(brain_mod, "_make", lambda key, config: available.get(key))
    return available


# ---------------------------------------------------------------------------
# Local model choice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (["llama3.2:3b", "qwen3.5:4b"], "qwen3.5:4b"),
        (["nomic-embed-text:latest", "mistral:7b"], "mistral:7b"),
        (["hf.co/someone/qwen3:8b"], "hf.co/someone/qwen3:8b"),
        (["deepseek-r1:8b"], "deepseek-r1:8b"),  # unknown family: first chat model
        (["nomic-embed-text", "mxbai-embed-large"], ""),
        ([], ""),
    ],
)
def test_pick_local_model(installed, expected):
    assert pick_local_model(installed) == expected


# ---------------------------------------------------------------------------
# Auto selection
# ---------------------------------------------------------------------------


def test_free_gemini_key_is_preferred(monkeypatch, engines):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    choice = select_brain(JarvisConfig())
    assert (choice.key, choice.engine_name) == ("gemini", "cloud")
    assert choice.model == BRAINS["gemini"].models[0]
    assert choice.describe().startswith("Google Gemini")


def test_google_api_key_also_counts(monkeypatch, engines):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    assert select_brain(JarvisConfig()).key == "gemini"


def test_falls_back_to_ollama_without_keys(engines):
    engines["ollama"] = _FakeOllama(["nomic-embed-text", "llama3.2:3b"])
    choice = select_brain(JarvisConfig())
    assert (choice.key, choice.model) == ("ollama", "llama3.2:3b")


def test_key_without_sdk_is_skipped_with_install_advice(monkeypatch, engines):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    engines["cloud"] = _FakeCloud(providers=("gpt",))  # google-genai missing
    with pytest.raises(BrainUnavailableError) as err:
        select_brain(JarvisConfig())
    assert "uv sync --extra jarvis --group desktop-native" in str(err.value)
    assert "google-genai" in str(err.value)


def test_nothing_available_explains_every_option(engines):
    with pytest.raises(BrainUnavailableError) as err:
        select_brain(JarvisConfig())
    message = str(err.value)
    assert "aistudio.google.com" in message
    assert "ollama pull qwen3.5:4b" in message
    assert "OPENAI_API_KEY" in message


def test_ollama_without_models_says_what_to_pull(engines):
    engines["ollama"] = _FakeOllama([])
    with pytest.raises(BrainUnavailableError, match="ollama pull qwen3.5:4b"):
        select_brain(JarvisConfig(), brain="ollama")


def test_configured_default_model_is_used_when_reachable(monkeypatch, engines):
    config = JarvisConfig()
    config.intelligence.default_model = "llama3.2:3b"
    seen = {}

    def fake_get_engine(cfg, engine_key=None, model=None):
        seen["args"] = (engine_key, model)
        return ("ollama", _FakeOllama([model]))

    monkeypatch.setattr("openjarvis.engine._discovery.get_engine", fake_get_engine)
    choice = select_brain(config)
    assert (choice.engine_name, choice.model) == ("ollama", "llama3.2:3b")
    assert seen["args"] == (None, "llama3.2:3b")


def test_unreachable_default_model_falls_back_with_a_note(monkeypatch, engines):
    config = JarvisConfig()
    config.intelligence.default_model = "qwen3.5:9b"
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    monkeypatch.setattr("openjarvis.engine._discovery.get_engine", lambda *a, **k: None)
    choice = select_brain(config)
    assert choice.key == "openai"
    assert any("qwen3.5:9b" in note for note in choice.notes)


# ---------------------------------------------------------------------------
# Explicit choices
# ---------------------------------------------------------------------------


def test_explicit_brain_ignores_others(monkeypatch, engines):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    engines["ollama"] = _FakeOllama(["qwen3.5:4b"])
    assert select_brain(JarvisConfig(), brain="ollama").key == "ollama"


def test_brain_from_config(monkeypatch, engines):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    config = JarvisConfig()
    config.jarvis.brain = "anthropic"
    choice = select_brain(config)
    assert (choice.key, choice.model) == ("anthropic", "claude-haiku-4-5")


def test_explicit_brain_unavailable_gives_targeted_help(engines):
    with pytest.raises(BrainUnavailableError) as err:
        select_brain(JarvisConfig(), brain="openai")
    message = str(err.value)
    assert "OPENAI_API_KEY is not set" in message
    assert "aistudio" not in message


def test_unknown_brain(engines):
    with pytest.raises(BrainUnavailableError, match="Unknown brain 'skynet'"):
        select_brain(JarvisConfig(), brain="skynet")


def test_engine_flag_must_match_the_brain(monkeypatch, engines):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    with pytest.raises(BrainUnavailableError, match="cannot run"):
        select_brain(JarvisConfig(), brain="gemini", engine_key="ollama")


def test_engine_flag_filters_auto_selection(monkeypatch, engines):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    engines["ollama"] = _FakeOllama(["qwen3.5:4b"])
    assert select_brain(JarvisConfig(), engine_key="ollama").key == "ollama"


def test_pinned_model_wins(monkeypatch, engines):
    calls = []

    def fake_get_engine(cfg, engine_key=None, model=None):
        calls.append((engine_key, model))
        return ("cloud", _FakeCloud())

    monkeypatch.setattr("openjarvis.engine._discovery.get_engine", fake_get_engine)
    config = JarvisConfig()
    config.jarvis.model = "gemini-4-hydrogen"
    choice = select_brain(config, brain="ollama")
    assert (choice.key, choice.model) == ("custom", "gemini-4-hydrogen")
    assert choice.label == "Google Gemini"
    assert select_brain(config, model="gpt-6.1-terra").model == "gpt-6.1-terra"
    assert calls == [("cloud", "gemini-4-hydrogen"), ("cloud", "gpt-6.1-terra")]


def test_pinned_cloud_model_never_goes_to_a_running_ollama(monkeypatch, engines):
    """Local engines accept any model name, so they must not get cloud models."""

    def fake_get_engine(cfg, engine_key=None, model=None):
        # Like the real get_engine: without a key it returns the default
        # engine (Ollama), which claims it can serve every model name.
        key = engine_key or "ollama"
        return (key, _FakeCloud() if key == "cloud" else _FakeOllama([model]))

    monkeypatch.setattr("openjarvis.engine._discovery.get_engine", fake_get_engine)
    cloud = ("gemini-3.8-flash", "gpt-6-luna", "claude-haiku-4-5", "openrouter/auto")
    for model in cloud:
        assert select_brain(JarvisConfig(), model=model).engine_name == "cloud", model
    local = ("qwen3.5:4b", "gpt-oss:20b", "hf.co/bartowski/Llama-3.2-3B-GGUF")
    for model in local:
        assert select_brain(JarvisConfig(), model=model).engine_name == "ollama", model
    # An explicit --engine is still obeyed.
    choice = select_brain(JarvisConfig(), model="gemini-3.8-flash", engine_key="ollama")
    assert choice.engine_name == "ollama"


def test_pinned_model_that_nothing_can_run(monkeypatch, engines):
    monkeypatch.setattr("openjarvis.engine._discovery.get_engine", lambda *a, **k: None)
    with pytest.raises(BrainUnavailableError, match="'gpt-6-luna'"):
        select_brain(JarvisConfig(), model="gpt-6-luna")


def test_detect_brains_reports_each_option(monkeypatch, engines):
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    report = {
        brain.key: (model, status)
        for brain, model, status in detect_brains(JarvisConfig())
    }
    assert report["openai"] == (BRAINS["openai"].models[0], "ready")
    assert report["gemini"][0] is None
    assert "not set" in report["gemini"][1]
    assert "not running" in report["ollama"][1]


def test_setup_help_for_one_brain():
    text = setup_help(only="gemini")
    assert "aistudio.google.com" in text
    assert "ollama" not in text.lower()


# ---------------------------------------------------------------------------
# cloud-keys.env
# ---------------------------------------------------------------------------


def test_load_saved_cloud_keys(tmp_path, monkeypatch):
    env_file = tmp_path / "cloud-keys.env"
    env_file.write_text(
        "\ufeff# my keys\n"
        'GEMINI_API_KEY = "g-from-file"\n'
        "export ANTHROPIC_API_KEY='a-from-file'\n"
        "OPENAI_API_KEY=o-from-file\n"
        "SOMETHING_ELSE=ignored\n"
        "OPENROUTER_API_KEY=\n"
        "not a key line\n",
        encoding="utf-8",
    )
    # The loader writes os.environ directly; snapshot and restore all of it.
    with patch.dict(os.environ, {"OPENAI_API_KEY": "from-env"}):
        loaded = load_saved_cloud_keys(env_file)
        assert sorted(loaded) == ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
        assert os.environ["GEMINI_API_KEY"] == "g-from-file"
        assert os.environ["ANTHROPIC_API_KEY"] == "a-from-file"
        assert os.environ["OPENAI_API_KEY"] == "from-env"  # env wins
        assert "SOMETHING_ELSE" not in os.environ
        assert "OPENROUTER_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ


def test_load_saved_cloud_keys_missing_file(tmp_path):
    assert load_saved_cloud_keys(tmp_path / "nope.env") == []
