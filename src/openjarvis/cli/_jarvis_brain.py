"""Choose JARVIS's "brain": the engine and model that answer requests.

JARVIS works with whichever brain the user has set up, so nobody has to
edit model names by hand:

* Google Gemini (a free key from https://aistudio.google.com/api-keys)
* OpenAI, Anthropic or OpenRouter (paid keys)
* Ollama, fully offline

Precedence: ``--model`` > ``[jarvis] model`` > ``--brain`` / ``[jarvis] brain``.
With ``brain = "auto"`` (the default) a configured
``[intelligence] default_model`` is used when something can serve it, then the
first available brain in :data:`AUTO_ORDER`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# One command for every JARVIS dependency. A partial ``uv sync`` (one extra
# only) would uninstall the voice packages and the native extension.
JARVIS_SYNC_COMMAND = "uv sync --extra jarvis --group desktop-native"


@dataclass(frozen=True)
class Brain:
    """A way of running JARVIS's language model."""

    key: str
    label: str
    engine: str
    env_vars: Tuple[str, ...] = ()
    # Fast, tool-capable models in order of preference. The first is used
    # unless a model is pinned. Voice needs low latency and free tiers have
    # small daily quotas, so these are the efficient tiers, not flagships.
    models: Tuple[str, ...] = ()
    setup: str = ""
    sdk_hint: str = ""


BRAINS: Dict[str, Brain] = {
    brain.key: brain
    for brain in (
        Brain(
            key="gemini",
            label="Google Gemini",
            engine="cloud",
            env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            # Flash-Lite has a much larger free daily quota than Flash.
            models=("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-4-hydrogen"),
            setup=(
                "Free: get a Google Gemini key at https://aistudio.google.com/api-keys"
                ' then run: setx GEMINI_API_KEY "your-key" (and open a new terminal)'
            ),
            sdk_hint=f'{JARVIS_SYNC_COMMAND}  (or: pip install "google-genai")',
        ),
        Brain(
            key="openai",
            label="OpenAI",
            engine="cloud",
            env_vars=("OPENAI_API_KEY",),
            models=("gpt-6-luna", "gpt-6.1-terra"),
            setup='Paid: setx OPENAI_API_KEY "sk-..." (and open a new terminal)',
            sdk_hint=f'{JARVIS_SYNC_COMMAND}  (or: pip install "openai")',
        ),
        Brain(
            key="anthropic",
            label="Anthropic Claude",
            engine="cloud",
            env_vars=("ANTHROPIC_API_KEY",),
            models=("claude-haiku-4-5",),
            setup='Paid: setx ANTHROPIC_API_KEY "sk-ant-..." (and open a new terminal)',
            sdk_hint=f'{JARVIS_SYNC_COMMAND}  (or: pip install "anthropic")',
        ),
        Brain(
            key="openrouter",
            label="OpenRouter",
            engine="cloud",
            env_vars=("OPENROUTER_API_KEY",),
            models=("openrouter/auto",),
            setup='Paid: setx OPENROUTER_API_KEY "sk-or-..." (and open a new terminal)',
            sdk_hint=f'{JARVIS_SYNC_COMMAND}  (or: pip install "openai")',
        ),
        Brain(
            key="ollama",
            label="Ollama (offline)",
            engine="ollama",
            setup=(
                "Offline: install Ollama from https://ollama.com, then run: "
                "ollama pull {local_model}"
            ),
        ),
    )
}

AUTO_ORDER: Tuple[str, ...] = ("gemini", "openai", "anthropic", "openrouter", "ollama")
BRAIN_CHOICES: Tuple[str, ...] = ("auto", *AUTO_ORDER)

# Local model families with reliable tool calling in Ollama, best first.
_LOCAL_TOOL_FAMILIES: Tuple[str, ...] = (
    "qwen3.6",
    "qwen3.5",
    "qwen3",
    "gpt-oss",
    "llama3.3",
    "llama3.2",
    "llama3.1",
    "mistral",
    "qwen2.5",
    "granite",
    "phi4-mini",
)
_NON_CHAT_MARKERS: Tuple[str, ...] = ("embed", "rerank", "whisper", "bge-", "clip")


class BrainUnavailableError(RuntimeError):
    """No brain could be used. ``str(error)`` is user-facing setup help."""


@dataclass
class BrainChoice:
    """The engine and model JARVIS will think with."""

    key: str  # brain key, or "custom" for an explicitly pinned model
    label: str
    engine_name: str
    engine: Any
    model: str
    notes: List[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.label} · {self.model}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CLOUD_KEY_NAMES = frozenset(
    name for brain in BRAINS.values() for name in brain.env_vars
)


def load_saved_cloud_keys(path: Any = None) -> List[str]:
    """Load API keys from ``<config dir>/cloud-keys.env`` into the environment.

    Lets keys be saved once in a file (``GEMINI_API_KEY=...`` per line)
    instead of a system environment variable. Variables that are already set
    win. Returns the names that were loaded.
    """
    from pathlib import Path

    if path is None:
        from openjarvis.core.paths import get_config_dir

        path = get_config_dir() / "cloud-keys.env"
    path = Path(path)
    if not path.is_file():
        return []
    loaded: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        name = name.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if name in _CLOUD_KEY_NAMES and value and not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded


def _key_present(brain: Brain) -> bool:
    return any((os.environ.get(name) or "").strip() for name in brain.env_vars)


def _make(engine_key: str, config: Any) -> Any:
    """Construct an engine quietly; ``None`` when it cannot be created."""
    import openjarvis.engine  # noqa: F401  # register engines
    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine._discovery import _make_engine

    if not EngineRegistry.contains(engine_key):
        return None
    try:
        return _make_engine(engine_key, config)
    except Exception as exc:
        logger.debug("Engine %r could not be created: %s", engine_key, exc)
        return None


def _healthy(engine: Any) -> bool:
    try:
        return bool(engine.health())
    except Exception as exc:
        logger.debug("Engine health check failed: %s", exc)
        return False


def _can_serve(engine: Any, model: str) -> bool:
    try:
        return bool(engine.can_serve(model))
    except Exception as exc:
        logger.debug("can_serve(%r) failed: %s", model, exc)
        return False


def pick_local_model(models: Sequence[str]) -> str:
    """Pick the installed local model most likely to handle tools well."""
    chat_models = [
        m for m in models if m and not any(x in m.lower() for x in _NON_CHAT_MARKERS)
    ]
    for family in _LOCAL_TOOL_FAMILIES:
        for name in chat_models:
            base = name.lower().split("/")[-1]
            if base == family or base.startswith((f"{family}:", f"{family}-")):
                return name
    return chat_models[0] if chat_models else ""


def recommended_local_model() -> str:
    """The Ollama model OpenJarvis recommends for this machine's memory."""
    try:
        from openjarvis.core.config import detect_hardware, recommend_model

        return recommend_model(detect_hardware(), "ollama") or "qwen3.5:4b"
    except Exception as exc:
        logger.debug("Hardware detection failed: %s", exc)
        return "qwen3.5:4b"


def _setup_line(brain: Brain) -> str:
    if brain.key == "ollama":
        return brain.setup.format(local_model=recommended_local_model())
    return brain.setup


def setup_help(problems: Sequence[str] = (), only: Optional[str] = None) -> str:
    """User-facing instructions for giving JARVIS a brain."""
    lines: List[str] = []
    if problems:
        lines.append("JARVIS could not find a usable brain:")
        lines.extend(f"  - {problem}" for problem in problems)
        lines.append("")
    keys = (only,) if only else ("gemini", "ollama", "openai")
    lines.append("To fix this, set up one of these:" if not only else "To set it up:")
    lines.extend(f"  * {_setup_line(BRAINS[key])}" for key in keys)
    if not only:
        lines.append(
            "Anthropic (ANTHROPIC_API_KEY) and OpenRouter (OPENROUTER_API_KEY) "
            "keys work too."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trying brains
# ---------------------------------------------------------------------------


def _try_cloud(brain: Brain, config: Any, problems: List[str]) -> Optional[BrainChoice]:
    if not _key_present(brain):
        problems.append(f"{brain.label}: {' or '.join(brain.env_vars)} is not set")
        return None
    engine = _make("cloud", config)
    model = brain.models[0]
    if engine is None or not _healthy(engine) or not _can_serve(engine, model):
        problems.append(
            f"{brain.label}: the API key is set but its Python package is missing. "
            f"Install it with: {brain.sdk_hint}"
        )
        return None
    return BrainChoice(brain.key, brain.label, "cloud", engine, model)


def _try_ollama(
    brain: Brain, config: Any, problems: List[str]
) -> Optional[BrainChoice]:
    engine = _make("ollama", config)
    if engine is None or not _healthy(engine):
        problems.append(
            "Ollama: not running (install it from https://ollama.com, or start it)"
        )
        return None
    try:
        installed = list(engine.list_models())
    except Exception as exc:
        logger.debug("Ollama list_models failed: %s", exc)
        installed = []
    model = pick_local_model(installed)
    if not model:
        problems.append(
            "Ollama: running, but no chat model is downloaded. Run: "
            f"ollama pull {recommended_local_model()}"
        )
        return None
    return BrainChoice(brain.key, brain.label, "ollama", engine, model)


def _try_brain(brain: Brain, config: Any, problems: List[str]) -> Optional[BrainChoice]:
    if brain.engine == "ollama":
        return _try_ollama(brain, config, problems)
    return _try_cloud(brain, config, problems)


# Providers whose models only the cloud engine can run. OpenRouter counts only
# with its explicit "openrouter/" prefix: other names with a slash, such as
# "hf.co/user/model", are valid Ollama models.
_CLOUD_ONLY_PROVIDERS = frozenset(
    {"openai", "anthropic", "google", "minimax", "atlascloud"}
)


def _cloud_only_model(model: str) -> bool:
    """True for a model no local engine can run (a Gemini, GPT or Claude ID)."""
    from openjarvis.server.cloud_router import get_provider

    return (
        model.startswith("openrouter/") or get_provider(model) in _CLOUD_ONLY_PROVIDERS
    )


def _choice_for_model(
    config: Any, model: str, engine_key: Optional[str]
) -> Optional[BrainChoice]:
    """An engine that can serve an explicitly chosen *model*, if any."""
    from openjarvis.engine._discovery import get_engine

    # get_engine tries the default engine first, and local engines accept any
    # model name. Without this, a pinned "gemini-3.8-flash" would be sent to a
    # running Ollama (the default engine) and fail on every request.
    if engine_key is None and _cloud_only_model(model):
        engine_key = "cloud"
    resolved = get_engine(config, engine_key, model=model)
    if resolved is None:
        return None
    engine_name, engine = resolved
    label = next(
        (
            b.label
            for b in BRAINS.values()
            if b.engine == engine_name and (b.engine == "ollama" or model in b.models)
        ),
        engine_name,
    )
    return BrainChoice("custom", label, engine_name, engine, model)


def _choice_for_engine(config: Any, engine_key: str) -> Optional[BrainChoice]:
    """First usable model on an explicitly chosen engine (vLLM, LM Studio...)."""
    engine = _make(engine_key, config)
    if engine is None or not _healthy(engine):
        return None
    try:
        models = list(engine.list_models())
    except Exception:
        models = []
    model = pick_local_model(models)
    if not model:
        return None
    return BrainChoice("custom", engine_key, engine_key, engine, model)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_brain(
    config: Any,
    *,
    brain: Optional[str] = None,
    model: Optional[str] = None,
    engine_key: Optional[str] = None,
) -> BrainChoice:
    """Return the brain JARVIS should use, or raise :class:`BrainUnavailableError`.

    *brain*, *model* and *engine_key* come from the command line and override
    the ``[jarvis]`` config section.
    """
    jarvis_cfg = getattr(config, "jarvis", None)
    pinned = (model or getattr(jarvis_cfg, "model", "") or "").strip()
    wanted = (brain or getattr(jarvis_cfg, "brain", "") or "auto").strip().lower()

    if pinned:
        choice = _choice_for_model(config, pinned, engine_key)
        if choice is None:
            raise BrainUnavailableError(
                f"Nothing can run the model {pinned!r} right now.\n"
                "Check its API key (or that Ollama is running and the model is "
                "pulled), or remove the pinned model to let JARVIS choose.\n\n"
                + setup_help()
            )
        return choice

    if wanted not in BRAIN_CHOICES:
        raise BrainUnavailableError(
            f"Unknown brain {wanted!r}. Choose one of: {', '.join(BRAIN_CHOICES)}."
        )

    problems: List[str] = []
    if wanted != "auto":
        target = BRAINS[wanted]
        if engine_key and engine_key != target.engine:
            raise BrainUnavailableError(
                f"--engine {engine_key} cannot run the {target.label} brain."
            )
        choice = _try_brain(target, config, problems)
        if choice is None:
            raise BrainUnavailableError(setup_help(problems, only=wanted))
        return choice

    notes: List[str] = []
    configured = (
        getattr(getattr(config, "intelligence", None), "default_model", "") or ""
    ).strip()
    if configured:
        choice = _choice_for_model(config, configured, engine_key)
        if choice is not None:
            return choice
        notes.append(
            f"Configured model {configured!r} is not reachable; choosing a brain "
            "automatically."
        )

    if engine_key and engine_key not in ("cloud", "ollama"):
        choice = _choice_for_engine(config, engine_key)
        if choice is None:
            raise BrainUnavailableError(
                f"Engine {engine_key!r} is not running or has no models."
            )
        choice.notes = notes
        return choice

    for key in AUTO_ORDER:
        candidate = BRAINS[key]
        if engine_key and candidate.engine != engine_key:
            continue
        choice = _try_brain(candidate, config, problems)
        if choice is not None:
            choice.notes = notes
            return choice
    raise BrainUnavailableError(setup_help(problems))


def detect_brains(config: Any) -> List[Tuple[Brain, Optional[str], str]]:
    """Report every brain as ``(brain, model or None, status)`` for display."""
    report: List[Tuple[Brain, Optional[str], str]] = []
    for key in AUTO_ORDER:
        problems: List[str] = []
        choice = _try_brain(BRAINS[key], config, problems)
        if choice is not None:
            report.append((BRAINS[key], choice.model, "ready"))
        else:
            reason = problems[0].split(": ", 1)[-1] if problems else "unavailable"
            report.append((BRAINS[key], None, reason))
    return report


__all__ = [
    "JARVIS_SYNC_COMMAND",
    "AUTO_ORDER",
    "BRAINS",
    "BRAIN_CHOICES",
    "Brain",
    "BrainChoice",
    "BrainUnavailableError",
    "detect_brains",
    "load_saved_cloud_keys",
    "pick_local_model",
    "recommended_local_model",
    "select_brain",
    "setup_help",
]
