"""``jarvis listen`` — the hands-free JARVIS voice assistant.

Say "Jarvis" followed by a request ("Jarvis, open Spotify", "Jarvis, what's
the weather?") and JARVIS answers out loud. After each answer it keeps
listening for a few seconds, so a follow-up does not need the wake word.

Speech is recognised locally with faster-whisper (or the OpenAI/Deepgram
APIs), and JARVIS speaks with Kokoro's British "bm_george" voice by default.
``--text`` gives the same assistant with typed input, for machines without a
microphone.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, List, Optional

import click
from rich.console import Console
from rich.markup import escape

from openjarvis.cli._jarvis_brain import JARVIS_SYNC_COMMAND

logger = logging.getLogger(__name__)

_INSTALL_VOICE_HINT = (
    f"Install voice support with:  {JARVIS_SYNC_COMMAND}\n"
    '(or: pip install "openjarvis[jarvis]").'
)
_MAX_SPOKEN_CHARS = 900


# ---------------------------------------------------------------------------
# Small helpers (pure functions; unit tested)
# ---------------------------------------------------------------------------


def speakable(text: str, *, limit: int = _MAX_SPOKEN_CHARS) -> str:
    """Turn a model reply into something pleasant to hear.

    Strips markdown, replaces raw links (which sound awful read aloud) and
    keeps long answers short; the full text is still printed on screen.
    """
    text = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.DOTALL)
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"https?://\S+", "the link on screen", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s*\n+\s*", ". ", text.strip())
    text = re.sub(r"\.(\s*\.)+", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: end + 1] if end > limit // 3 else cut.rstrip() + "…") + (
            " The rest is on screen."
        )
    return text


def friendly_error(exc: BaseException, honorific: str = "sir") -> str:
    """A short spoken explanation for an engine/tool failure."""
    message = str(exc).lower()
    title = f", {honorific}" if honorific else ""
    if any(
        marker in message
        for marker in ("429", "resource_exhausted", "rate limit", "quota", "too many")
    ):
        return (
            f"I've reached my usage limit for the moment{title}. "
            "Give it a minute, or switch me to another brain."
        )
    if any(
        marker in message
        for marker in ("401", "403", "api key", "api_key", "unauthorized", "permission")
    ):
        return f"My API key was rejected{title}. Please check it and restart me."
    if any(
        marker in message
        for marker in ("connect", "timed out", "timeout", "unreachable", "network")
    ):
        return f"I can't reach my brain right now{title}. Check the connection."
    return f"I'm afraid something went wrong{title}. The details are on screen."


def greeting(honorific: str = "sir", now: Optional[datetime] = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        part = "Good morning"
    elif 12 <= hour < 18:
        part = "Good afternoon"
    else:
        part = "Good evening"
    title = f", {honorific}" if honorific else ""
    return f"{part}{title}. JARVIS online and at your service."


def tolerate_narrow_output_encoding(streams: Optional[List[Any]] = None) -> None:
    """Replace characters the output encoding lacks instead of crashing.

    In a console, Windows Python writes Unicode directly. Redirected output
    (a pipe or a file) uses the ANSI code page instead, e.g. cp1252, which
    cannot encode the symbols JARVIS prints (✓, ◉, ━); printing them would
    raise UnicodeEncodeError and stop the assistant.
    """
    for stream in streams if streams is not None else [sys.stdout, sys.stderr]:
        encoding = (getattr(stream, "encoding", None) or "").lower()
        if encoding.replace("-", "").replace("_", "") in ("utf8", "utf8sig"):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError) as exc:
            logger.debug("Could not relax the encoding of %r: %s", stream, exc)


def _transcribe(
    backend: Any, audio: bytes, *, language: Optional[str], prompt: str
) -> str:
    """Transcribe with optional prompt/VAD support when the backend has it."""
    params = inspect.signature(backend.transcribe).parameters
    kwargs: dict = {"format": "wav", "language": language}
    if "prompt" in params:
        kwargs["prompt"] = prompt
    if "vad_filter" in params:
        kwargs["vad_filter"] = True
    result = backend.transcribe(audio, **kwargs)
    return (getattr(result, "text", "") or "").strip()


# ---------------------------------------------------------------------------
# The conversation loop (I/O injected so it is testable without hardware)
# ---------------------------------------------------------------------------


@dataclass
class VoiceLoop:
    """Wake word -> request -> answer -> follow-up window, until told to stop.

    ``hear(wait_seconds)`` returns a transcript, ``""`` for nothing usable, or
    ``None`` when *wait_seconds* passed in silence. ``ask(request, history)``
    returns JARVIS's reply. ``say`` speaks; ``show(kind, text)`` displays.
    """

    hear: Callable[[Optional[float]], Optional[str]]
    ask: Callable[[str, List[Any]], str]
    say: Callable[[str], None]
    show: Callable[[str, str], None]
    detector: Any
    require_wake_word: bool = True
    follow_up_seconds: float = 8.0
    honorific: str = "sir"
    clock: Callable[[], float] = time.monotonic
    max_history: int = 12
    history: List[Any] = field(default_factory=list)
    _awake_until: float = 0.0
    _acks: int = 0

    # -- state ----------------------------------------------------------

    def awake(self) -> bool:
        return not self.require_wake_word or self.clock() < self._awake_until

    def _stay_awake(self) -> None:
        if self.require_wake_word and self.follow_up_seconds > 0:
            self._awake_until = self.clock() + self.follow_up_seconds

    def _sleep(self) -> None:
        self._awake_until = 0.0

    def _title(self) -> str:
        return f", {self.honorific}" if self.honorific else ""

    def _reply(self, text: str) -> None:
        self.show("jarvis", text)
        self.say(text)

    def _ack(self) -> str:
        options = (
            f"Yes{self._title()}?",
            "At your service.",
            f"I'm listening{self._title()}.",
        )
        self._acks += 1
        return options[(self._acks - 1) % len(options)]

    # -- one transcript ---------------------------------------------------

    def handle(self, transcript: str) -> str:
        """Process one transcript; returns what happened (for tests/logging)."""
        from openjarvis.speech.wake_word import (
            is_cancel_command,
            is_noise_transcript,
            is_stop_command,
        )

        text = (transcript or "").strip()
        if not text or is_noise_transcript(text):
            return "ignored"

        match = self.detector.find(text)
        if match is not None:
            request = match.command
        elif self.awake():
            request = text
        else:
            self.show("overheard", text)
            return "ignored"

        if is_stop_command(request or text):
            self._reply(f"Very good{self._title()}. Signing off.")
            return "stop"
        if not request:
            self.show("you", text)
            self._reply(self._ack())
            self._stay_awake()
            return "ack"
        if is_cancel_command(request):
            self._reply(f"Very well{self._title()}.")
            self._sleep()
            return "cancel"

        self.show("you", request)
        try:
            reply = self.ask(request, list(self.history))
        except Exception as exc:  # engine/tool failure: tell the user, carry on
            logger.debug("JARVIS request failed", exc_info=True)
            self.show("error", f"{type(exc).__name__}: {exc}")
            self.say(friendly_error(exc, self.honorific))
            self._stay_awake()
            return "error"

        reply = (reply or "").strip() or f"Done{self._title()}."
        self._remember(request, reply)
        self._reply(reply)
        self._stay_awake()
        return "answered"

    def _remember(self, request: str, reply: str) -> None:
        from openjarvis.core.types import Message, Role

        self.history.append(Message(role=Role.USER, content=request))
        self.history.append(Message(role=Role.ASSISTANT, content=reply))
        if len(self.history) > self.max_history:
            del self.history[: len(self.history) - self.max_history]

    # -- main loop -------------------------------------------------------------

    def run(self, *, max_rounds: Optional[int] = None) -> None:
        rounds = 0
        was_awake = None
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
            awake = self.awake()
            if awake != was_awake:
                self.show("state", "listening" if awake else "standby")
                was_awake = awake
            wait = None
            if self.require_wake_word and awake:
                wait = max(0.5, self._awake_until - self.clock())
            transcript = self.hear(wait)
            if transcript is None:
                if self.require_wake_word:
                    self._sleep()
                continue
            if self.handle(transcript) == "stop":
                return


# ---------------------------------------------------------------------------
# Wiring: brain, agent, microphone, voice
# ---------------------------------------------------------------------------


def _build_agent(config: Any, choice: Any, bus: Any, tools_option: Optional[str]):
    """Construct a fully secured JarvisAgent, mirroring ``jarvis chat``."""
    import openjarvis.agents  # noqa: F401  # register agents
    from openjarvis.agents.jarvis import JarvisAgent, build_jarvis_tools
    from openjarvis.cli._tool_names import resolve_tool_names
    from openjarvis.prompt.builder import SystemPromptBuilder
    from openjarvis.security import setup_security
    from openjarvis.security.runtime import agent_security_kwargs, wire_agent_security

    security = setup_security(config, choice.engine, bus)
    names = resolve_tool_names(
        tools_option,
        getattr(config.tools, "enabled", None),
        getattr(config.agent, "tools", None),
    )
    tools = build_jarvis_tools(names, config=config) if names else None

    kwargs: dict = {
        "bus": bus,
        "tools": tools,
        "max_turns": max(2, min(config.agent.max_turns, 8)),
        "prompt_builder": SystemPromptBuilder(
            agent_template=config.agent.default_system_prompt or "",
            memory_files_config=config.memory_files,
            system_prompt_config=config.system_prompt,
        ),
        "honorific": config.jarvis.honorific,
    }
    kwargs.update(
        agent_security_kwargs(
            JarvisAgent,
            capability_policy=security.capability_policy,
            rate_limiter=security.rate_limiter,
            agent_id="jarvis",
        )
    )
    agent = JarvisAgent(security.engine, choice.model, **kwargs)
    wire_agent_security(
        agent,
        bus=bus,
        capability_policy=security.capability_policy,
        rate_limiter=security.rate_limiter,
        agent_id="jarvis",
    )
    return agent


def _make_show(
    console: Console, verbose: bool, *, typed: bool = False
) -> Callable[[str, str], None]:
    def show(kind: str, text: str) -> None:
        safe = escape(text)
        if typed and kind in ("state", "you"):
            return  # the user can already see what they typed
        if kind == "state":
            if text == "listening":
                console.print("[bold cyan]◉ Listening…[/bold cyan]")
            else:
                console.print("[dim cyan]○ Standing by — say “Jarvis”.[/dim cyan]")
        elif kind == "you":
            console.print(f"[bold]You:[/bold] {safe}")
        elif kind == "jarvis":
            console.print(f"[bold cyan]JARVIS:[/bold cyan] {safe}\n")
        elif kind == "error":
            console.print(f"[red]{safe}[/red]")
        elif kind == "overheard":
            if verbose:
                console.print(f"[dim](heard: {safe})[/dim]")
        else:
            console.print(f"[dim]{safe}[/dim]")

    return show


def _check(console: Console, config: Any) -> None:
    """Print what JARVIS can use on this machine, then exit."""
    from openjarvis.cli._jarvis_brain import detect_brains, setup_help

    console.print("[bold cyan]JARVIS systems check[/bold cyan]\n")
    console.print("[bold]Brains[/bold]")
    any_brain = False
    for brain, model, status in detect_brains(config):
        if model:
            any_brain = True
            console.print(f"  [green]✓[/green] {brain.label}: {escape(model)}")
        else:
            console.print(f"  [dim]✗ {brain.label}: {escape(status)}[/dim]")
    if not any_brain:
        console.print(f"\n[yellow]{escape(setup_help())}[/yellow]")
    console.print("\n[bold]Voice[/bold]")
    voice_missing = False
    for module, label in (
        ("sounddevice", "Microphone and speakers (sounddevice)"),
        ("faster_whisper", "Local speech recognition (faster-whisper)"),
        ("kokoro", "British voice (kokoro)"),
    ):
        ok = importlib.util.find_spec(module) is not None
        voice_missing = voice_missing or not ok
        mark = "[green]✓[/green]" if ok else "[yellow]✗[/yellow]"
        console.print(f"  {mark} {label}")
    if voice_missing:
        console.print(f"\n[yellow]{escape(_INSTALL_VOICE_HINT)}[/yellow]")
    console.print(
        f"\nWake word: “{escape(config.jarvis.wake_word)}”   "
        f"Voice: {escape(config.speech.tts_backend)} / "
        f"{escape(config.speech.voice_id)}"
    )


@click.command("listen")
@click.option(
    "--brain",
    type=click.Choice(
        ["auto", "gemini", "openai", "anthropic", "openrouter", "ollama"]
    ),
    default=None,
    help="Which AI answers (default: [jarvis] brain, normally auto).",
)
@click.option("-m", "--model", "model_name", default=None, help="Pin an exact model.")
@click.option("-e", "--engine", "engine_key", default=None, help="Engine backend.")
@click.option("--tools", default=None, help="Comma-separated tool names.")
@click.option(
    "--text",
    "text_mode",
    is_flag=True,
    default=False,
    help="Type requests instead of speaking (no microphone needed).",
)
@click.option("--mute", is_flag=True, default=False, help="Don't speak replies.")
@click.option(
    "--no-wake-word",
    is_flag=True,
    default=False,
    help="Treat everything heard as a request (no need to say “Jarvis”).",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Show which brains and voice features are available, then exit.",
)
@click.option(
    "--verbose-heard",
    is_flag=True,
    default=False,
    help="Show speech that didn't include the wake word.",
)
def listen(
    brain: Optional[str],
    model_name: Optional[str],
    engine_key: Optional[str],
    tools: Optional[str],
    text_mode: bool,
    mute: bool,
    no_wake_word: bool,
    check: bool,
    verbose_heard: bool,
) -> None:
    """Start JARVIS: say “Jarvis” and ask for anything.

    \b
    Examples:
      jarvis listen                 voice mode with the wake word
      jarvis listen --text          type instead of talking
      jarvis listen --brain ollama  run fully offline
      jarvis listen --check         see what's available
    """
    from openjarvis.cli._jarvis_brain import load_saved_cloud_keys
    from openjarvis.core.config import load_config
    from openjarvis.core.credentials import inject_credentials

    tolerate_narrow_output_encoding()
    console = Console()
    inject_credentials()
    load_saved_cloud_keys()
    config = load_config()
    jarvis_cfg = config.jarvis

    if check:
        _check(console, config)
        return

    import openjarvis.engine  # noqa: F401  # register engines
    from openjarvis.cli._jarvis_brain import BrainUnavailableError, select_brain
    from openjarvis.core.events import EventBus
    from openjarvis.intelligence import register_builtin_models

    register_builtin_models()
    with console.status("[cyan]Choosing a brain…[/cyan]"):
        try:
            choice = select_brain(
                config, brain=brain, model=model_name, engine_key=engine_key
            )
        except BrainUnavailableError as exc:
            console.print(f"[yellow]{escape(str(exc))}[/yellow]")
            sys.exit(1)
    for note in choice.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")

    bus = EventBus(record_history=False)
    agent = _build_agent(config, choice, bus, tools)
    honorific = (jarvis_cfg.honorific or "").strip()

    # ---- voice output -------------------------------------------------------
    from openjarvis.cli._voice_chat import VoiceSession, speak

    session = VoiceSession(config)
    if not mute:
        with console.status("[cyan]Warming up my voice…[/cyan]"):
            try:
                tts_ready = session.get_tts_backend() is not None
            except Exception as exc:
                logger.debug("TTS discovery failed: %s", exc)
                tts_ready = False
        if not tts_ready:
            console.print(
                "[yellow]No voice available, so I'll reply in text only.[/yellow]\n"
                f"[dim]{escape(_INSTALL_VOICE_HINT)}[/dim]"
            )
            mute = True

    def say(text: str) -> None:
        if mute or not text:
            return
        try:
            speak(speakable(text), console, session)
        except Exception as exc:
            console.print(f"[dim yellow]Voice error: {escape(str(exc))}[/dim yellow]")

    # ---- voice input --------------------------------------------------------
    from openjarvis.speech.wake_word import WakeWordDetector

    detector = WakeWordDetector(jarvis_cfg.wake_word)
    wake_word_label = jarvis_cfg.wake_word.strip().title() or "Jarvis"
    raw_language = (config.speech.language or "en").strip()
    language = None if raw_language.lower() == "auto" else raw_language
    stt = None
    threshold = float(jarvis_cfg.mic_threshold or 0)

    if not text_mode:
        if importlib.util.find_spec("sounddevice") is None:
            console.print(
                "[yellow]Microphone support isn't installed, so I'll take typed "
                f"requests.[/yellow]\n[dim]{escape(_INSTALL_VOICE_HINT)}[/dim]"
            )
            text_mode = True
        else:
            with console.status("[cyan]Loading speech recognition…[/cyan]"):
                try:
                    stt = session.get_stt_backend()
                except Exception as exc:
                    logger.debug("STT discovery failed: %s", exc)
                    stt = None
            if stt is None:
                console.print(
                    "[yellow]Speech recognition isn't available, so I'll take "
                    f"typed requests.[/yellow]\n[dim]{escape(_INSTALL_VOICE_HINT)}"
                    "[/dim]"
                )
                text_mode = True

    if not text_mode and threshold <= 0:
        from openjarvis.speech.voice_io import measure_noise_floor

        with console.status("[cyan]Calibrating the microphone — quiet please…[/cyan]"):
            try:
                floor = measure_noise_floor(seconds=1.0)
                threshold = min(2500.0, max(350.0, floor * 3.0))
            except Exception as exc:
                console.print(
                    f"[yellow]Microphone error: {escape(str(exc))}[/yellow]\n"
                    "[dim]Check that a microphone is connected and allowed in "
                    "Windows Settings › Privacy › Microphone. Using typed "
                    "requests for now.[/dim]"
                )
                text_mode = True

    def hear_voice(wait_seconds: Optional[float]) -> Optional[str]:
        from openjarvis.speech.voice_io import record_utterance

        audio = record_utterance(
            silence_threshold=threshold,
            wait_seconds=wait_seconds,
            silence_seconds=0.9,
            max_seconds=20.0,
        )
        if audio is None:
            return None if wait_seconds is not None else ""
        try:
            return _transcribe(
                stt, audio, language=language, prompt=f"{wake_word_label}."
            )
        except Exception as exc:
            console.print(f"[red]Transcription error: {escape(str(exc))}[/red]")
            return ""

    def hear_text(_wait: Optional[float]) -> Optional[str]:
        try:
            typed = input("You> ")
        except EOFError:
            return "goodbye"
        return typed.strip()

    def ask(request: str, history: List[Any]) -> str:
        from openjarvis.agents._stubs import AgentContext
        from openjarvis.memory import publish_completed_exchange

        context = AgentContext()
        for message in history:
            context.conversation.add(message)
        with console.status("[cyan]Thinking…[/cyan]"):
            result = agent.run(request, context=context)
        content = getattr(result, "content", None) or str(result)
        publish_completed_exchange(bus, request, content, source="cli.listen")
        return content

    # ---- go -------------------------------------------------------------------
    voice_label = (
        "muted"
        if mute
        else f"{config.speech.tts_backend} / {config.speech.voice_id or 'default'}"
    )
    mode = "typed requests" if text_mode else f"say “{wake_word_label}”"
    if not text_mode and no_wake_word:
        mode = "always listening (no wake word)"
    console.print(
        "\n[bold cyan]━━━━━━━━━━━━  J.A.R.V.I.S.  ━━━━━━━━━━━━[/bold cyan]\n"
        f"  Brain: [cyan]{escape(choice.describe())}[/cyan]\n"
        f"  Voice: [cyan]{escape(voice_label)}[/cyan]   "
        f"Input: [cyan]{escape(mode)}[/cyan]\n"
        "  Say or type “goodbye” to stop. Ctrl+C also works.\n"
    )

    loop = VoiceLoop(
        hear=hear_text if text_mode else hear_voice,
        ask=ask,
        say=say,
        show=_make_show(console, verbose_heard, typed=text_mode),
        detector=detector,
        require_wake_word=not (text_mode or no_wake_word),
        follow_up_seconds=float(jarvis_cfg.follow_up_seconds),
        honorific=honorific,
    )
    if jarvis_cfg.greeting:
        hello = greeting(honorific)
        console.print(f"[bold cyan]JARVIS:[/bold cyan] {escape(hello)}\n")
        say(hello)
    try:
        loop.run()
    except KeyboardInterrupt:
        console.print("\n[dim]JARVIS signing off.[/dim]")


__all__ = ["VoiceLoop", "friendly_error", "greeting", "listen", "speakable"]
