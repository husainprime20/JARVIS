"""JarvisAgent: a voice-first personal assistant that can act on the computer.

JARVIS is an :class:`~openjarvis.agents.orchestrator.OrchestratorAgent` with:

* a British, butler-style persona tuned for being *spoken aloud* (short
  sentences, no markdown);
* desktop tools by default (open apps and websites, play YouTube, media and
  volume keys, system stats, time, weather, web search, calculator);
* long-term memory: facts it saves with ``memory_manage`` and
  ``user_profile_manage`` are folded back into every system prompt;
* the current local date and time in every system prompt.

Unlike most agents, JARVIS composes its own system prompt on every turn
instead of delegating to the injected ``SystemPromptBuilder``: the builder's
template is the generic "You are OpenJarvis" identity, so JARVIS uses the
builder only for the SOUL/MEMORY/USER persona sections.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tools._stubs import BaseTool

logger = logging.getLogger(__name__)

DEFAULT_JARVIS_TOOLS: tuple[str, ...] = (
    "open_app",
    "open_website",
    "play_youtube",
    "system_control",
    "system_stats",
    "current_datetime",
    "get_weather",
    "web_search",
    "calculator",
    "memory_manage",
    "user_profile_manage",
)

JARVIS_SYSTEM_PROMPT = """\
You are JARVIS ("Jarvis"), the user's personal AI assistant, running on their \
own computer. You are modelled on the AI butler from Iron Man: loyal, calm, \
efficient and dry-witted, with a warm British sensibility. You are polite but \
never obsequious, and witty but never frivolous. Your humour is understated.

HOW YOU SPEAK
- Your replies are usually read aloud by a text-to-speech voice. Write the way \
a person talks, in short natural sentences. Never use markdown, bullet points, \
headings, tables, emojis or code blocks unless the user asks for written output.
- Be brief: one to three sentences for most requests. Lead with the answer and \
only add detail that helps.
- Prefer spoken words to symbols and never read out long web addresses.

WHAT YOU DO
- When a request matches one of your tools, use the tool instead of telling \
the user how to do it themselves. Do several things in one go when asked.
- After acting, confirm the outcome in one short sentence, for example \
"Spotify is open." If a tool fails, say so plainly and offer one next step.
- Never claim you did something you did not do, and never invent facts. If \
you cannot find out, say so. Use web_search for news and current information.
- If a request is ambiguous, make a sensible assumption, or ask one short \
question if a mistake would matter.

MEMORY
- When the user shares a lasting fact about themselves (their name, what to \
call you or them, where they live, their work, family, tastes or routines), \
save it with user_profile_manage without being asked, then carry on.
- When they ask you to remember anything else, save it with memory_manage. \
When they ask you to forget something, remove it.
- Use what you know naturally, for example their city for the weather, \
without reciting it back.
- Never store passwords, bank or card numbers, or other secrets.

IDENTITY
- You are Jarvis. If asked what you are, you are the user's personal AI \
assistant, powered by OpenJarvis and running on their computer."""

# One line per tool, listed in the prompt only for tools JARVIS really has.
_TOOL_HINTS: Dict[str, str] = {
    "open_app": "open_app: launch programs (Spotify, Chrome, Notepad, Settings...)",
    "open_website": "open_website: open a website or a Google search",
    "play_youtube": "play_youtube: play a song or video on YouTube",
    "system_control": (
        "system_control: volume, mute, play/pause, next or previous track, "
        "lock the screen"
    ),
    "system_stats": "system_stats: CPU, memory, disk, battery and uptime",
    "current_datetime": "current_datetime: the time in other cities and time zones",
    "get_weather": "get_weather: current weather and forecasts",
    "web_search": "web_search: news, live facts and anything you are unsure of",
    "calculator": "calculator: exact arithmetic",
    "memory_manage": "memory_manage: your long-term notes",
    "user_profile_manage": "user_profile_manage: what you know about the user",
}


def _memory_file_paths(config: Any) -> tuple[Optional[str], Optional[str]]:
    """Return the MEMORY.md / USER.md paths the prompt builder reads."""
    memory_files = getattr(config, "memory_files", None)
    if memory_files is None:
        return None, None
    try:
        from openjarvis.prompt.builder import SystemPromptBuilder

        resolved = SystemPromptBuilder._resolve_persona(memory_files)
    except Exception as exc:  # invalid persona name: fall back to defaults
        logger.debug("Could not resolve persona memory paths: %s", exc)
        return None, None
    return resolved.memory_path or None, resolved.user_path or None


def build_jarvis_tools(
    names: Sequence[str] = DEFAULT_JARVIS_TOOLS,
    *,
    config: Any = None,
) -> List[BaseTool]:
    """Instantiate JARVIS's tools, skipping any that are unavailable.

    Memory tools are pointed at the same files the system prompt reads, so a
    saved fact is visible on the very next turn even with a named persona.
    """
    import openjarvis.tools  # noqa: F401  # trigger @ToolRegistry.register
    from openjarvis.core.registry import ToolRegistry

    if config is None:
        try:
            from openjarvis.core.config import load_config

            config = load_config()
        except Exception as exc:
            logger.debug("JARVIS tools: config unavailable (%s)", exc)
            config = None
    memory_path, user_path = _memory_file_paths(config)

    tools: List[BaseTool] = []
    for name in names:
        if not ToolRegistry.contains(name):
            logger.debug("JARVIS tool %r is not installed; skipping", name)
            continue
        entry = ToolRegistry.get(name)
        if isinstance(entry, BaseTool):
            tools.append(entry)
            continue
        from openjarvis.tools._paths import file_tool_kwargs

        kwargs: Dict[str, Any] = dict(file_tool_kwargs(name, config))
        if name == "memory_manage" and memory_path:
            kwargs["memory_path"] = memory_path
        elif name == "user_profile_manage" and user_path:
            kwargs["user_path"] = user_path
        try:
            tools.append(entry(**kwargs))
        except Exception as exc:
            logger.warning("JARVIS tool %r could not be created: %s", name, exc)
    return tools


@AgentRegistry.register("jarvis")
class JarvisAgent(OrchestratorAgent):
    """Voice-first personal assistant with desktop control and memory."""

    agent_id = "jarvis"
    # Tool names used when the caller configures none (see ``jarvis serve``).
    default_tools: tuple[str, ...] = DEFAULT_JARVIS_TOOLS
    _default_temperature = 0.6
    _default_max_tokens = 1024
    _default_max_turns = 8

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: Optional[List[BaseTool]] = None,
        bus: Optional[EventBus] = None,
        max_turns: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        prompt_builder: Optional[Any] = None,
        parallel_tools: bool = True,
        interactive: bool = False,
        confirm_callback=None,
        capability_policy: Optional[Any] = None,
        agent_id: Optional[str] = None,
        rate_limiter: Optional[Any] = None,
        before_tool_call: Optional[Callable[[str, dict[str, Any]], bool]] = None,
        honorific: Optional[str] = None,
        include_persona: bool = True,
    ) -> None:
        config = None
        if tools is None or honorific is None:
            try:
                from openjarvis.core.config import load_config

                config = load_config()
            except Exception as exc:
                logger.debug("JarvisAgent: config unavailable (%s)", exc)
        if tools is None:
            tools = build_jarvis_tools(config=config)
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns,
            temperature=temperature,
            max_tokens=max_tokens,
            mode="function_calling",
            # JARVIS assembles its own prompt in _build_messages; see module
            # docstring for why the builder must not replace it.
            system_prompt=None,
            prompt_builder=None,
            parallel_tools=parallel_tools,
            interactive=interactive,
            confirm_callback=confirm_callback,
            capability_policy=capability_policy,
            agent_id=agent_id,
            rate_limiter=rate_limiter,
            before_tool_call=before_tool_call,
        )
        self._jarvis_prompt = (system_prompt or "").strip() or JARVIS_SYSTEM_PROMPT
        self._persona_builder = prompt_builder
        self._include_persona = include_persona
        if honorific is None:
            jarvis_cfg = getattr(config, "jarvis", None)
            honorific = getattr(jarvis_cfg, "honorific", "sir")
        self._honorific = (honorific or "").strip()

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _persona_text(self) -> str:
        """SOUL / MEMORY / USER sections, re-read on every turn."""
        if not self._include_persona:
            return ""
        builder = self._persona_builder
        if builder is None:
            try:
                from openjarvis.core.config import load_config
                from openjarvis.prompt.builder import SystemPromptBuilder

                cfg = load_config()
                builder = SystemPromptBuilder(
                    agent_template="",
                    memory_files_config=cfg.memory_files,
                    system_prompt_config=cfg.system_prompt,
                )
            except Exception as exc:
                logger.debug("JarvisAgent: persona unavailable (%s)", exc)
                return ""
            # Cache the builder itself; persona_sections() still re-reads
            # the files each turn, so new memories show up immediately.
            self._persona_builder = builder
        try:
            return builder.persona_sections()
        except Exception as exc:
            # A broken persona file must not take the assistant down.
            logger.warning("JarvisAgent: could not read persona files: %s", exc)
            return ""

    def compose_system_prompt(self, *, now: Optional[datetime] = None) -> str:
        """Return the full JARVIS system prompt for one turn."""
        from openjarvis.tools.pc_control import (
            format_utc_offset,
            spoken_date,
            spoken_time,
        )

        moment = now or datetime.now().astimezone()
        parts = [self._jarvis_prompt]
        if self._honorific:
            parts.append(
                f'Address the user as "{self._honorific}" now and then, as a '
                "butler would (not in every sentence), unless their profile "
                "says they prefer something else."
            )
        hints = [
            _TOOL_HINTS[tool.spec.name]
            for tool in self._tools
            if tool.spec.name in _TOOL_HINTS
        ]
        if hints:
            parts.append("## Your tools\n\n" + "\n".join(hints))
        parts.append(
            "## Right now\n\n"
            f"It is {spoken_time(moment)} on {spoken_date(moment)} "
            f"(UTC{format_utc_offset(moment)}). Use this for questions about "
            "today's date or the local time."
        )
        persona = self._persona_text()
        if persona:
            parts.append(persona)
        return "\n\n".join(parts)

    def _build_messages(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        *,
        system_prompt: Optional[str] = None,
    ) -> list[Message]:
        # Built per call (never stored): one agent instance serves many
        # requests on the server, and the time and memory change between them.
        return super()._build_messages(
            input,
            context,
            system_prompt=self.compose_system_prompt(),
        )

    def _begin_tool_session_from_messages(self, messages: List[Message]) -> None:
        """Seed tool taint from the conversation, not from JARVIS's own prompt.

        The system prompt is assembled locally from the user's persona and
        memory files. Once the user asked JARVIS to remember, say, their phone
        number, seeding taint from USER.md would block every PII sink
        (``web_search``, ``open_website``) on every later turn, permanently,
        because that file never ages out of the prompt the way chat history
        does. The user's messages and all tool results are still tracked, so
        private data surfaced during a run keeps gating those sinks.
        """
        super()._begin_tool_session_from_messages(
            [message for message in messages if message.role != Role.SYSTEM]
        )


__all__ = [
    "DEFAULT_JARVIS_TOOLS",
    "JARVIS_SYSTEM_PROMPT",
    "JarvisAgent",
    "build_jarvis_tools",
]
