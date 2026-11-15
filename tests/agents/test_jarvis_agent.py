"""Tests for JarvisAgent (agents/jarvis.py)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.agents import jarvis as jarvis_module
from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.jarvis import (
    DEFAULT_JARVIS_TOOLS,
    JarvisAgent,
    build_jarvis_tools,
)
from openjarvis.core.config import JarvisConfig, MemoryFilesConfig
from openjarvis.core.registry import AgentRegistry, ToolRegistry
from openjarvis.core.types import Message, Role
from openjarvis.prompt.builder import SystemPromptBuilder
from openjarvis.tools import pc_control
from openjarvis.tools.pc_control import CurrentDateTimeTool, SystemStatsTool
from tests.agents.fake_engine import FakeEngine

_FROZEN = datetime(2026, 11, 15, 14, 5, tzinfo=timezone(timedelta(hours=5), "PKT"))
_OPENJARVIS_IDENTITY = "identify yourself as OpenJarvis"


def _builder(tmp_path, *, user: str = "", memory: str = "") -> SystemPromptBuilder:
    """A builder like serve/chat inject: generic template + persona files."""
    soul = tmp_path / "SOUL.md"
    soul.write_text("# Agent Persona\n\nYou are Jarvis.\n", encoding="utf-8")
    user_md = tmp_path / "USER.md"
    user_md.write_text(f"# User Profile\n\n{user}", encoding="utf-8")
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text(f"# Agent Memory\n\n{memory}", encoding="utf-8")
    return SystemPromptBuilder(
        agent_template=JarvisConfig().agent.default_system_prompt,
        memory_files_config=MemoryFilesConfig(
            soul_path=str(soul),
            memory_path=str(memory_md),
            user_path=str(user_md),
        ),
    )


def _system_text(messages: list[Message]) -> str:
    assert messages[0].role == Role.SYSTEM
    assert sum(1 for m in messages if m.role == Role.SYSTEM) == 1
    return messages[0].content


def _agent(engine=None, **kwargs) -> JarvisAgent:
    kwargs.setdefault("tools", [CurrentDateTimeTool(), SystemStatsTool()])
    kwargs.setdefault("honorific", "sir")
    return JarvisAgent(engine or FakeEngine([{"content": "Done."}]), "m", **kwargs)


def _reload_jarvis_tool_modules() -> None:
    """Re-run @ToolRegistry.register after conftest cleared the registry."""
    import openjarvis.tools  # noqa: F401

    for module in (
        "openjarvis.tools.pc_control",
        "openjarvis.tools.weather",
        "openjarvis.tools.web_search",
        "openjarvis.tools.calculator",
        "openjarvis.tools.memory_manage",
        "openjarvis.tools.user_profile_manage",
    ):
        importlib.reload(importlib.import_module(module))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registered_as_jarvis():
    module = importlib.reload(jarvis_module)
    assert AgentRegistry.contains("jarvis")
    agent_cls = AgentRegistry.get("jarvis")
    assert agent_cls is module.JarvisAgent
    assert agent_cls.accepts_tools is True
    assert agent_cls.default_tools == DEFAULT_JARVIS_TOOLS


def test_package_import_registers_jarvis():
    import openjarvis.agents as agents_pkg

    importlib.reload(agents_pkg)
    importlib.reload(jarvis_module)
    assert AgentRegistry.contains("jarvis")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def test_jarvis_persona_replaces_the_generic_openjarvis_identity(tmp_path):
    agent = _agent(prompt_builder=_builder(tmp_path))
    system = _system_text(agent._build_messages("hello"))
    assert "You are JARVIS" in system
    assert _OPENJARVIS_IDENTITY not in system
    assert "Never use markdown" in system


def test_prompt_lists_only_the_tools_jarvis_has(tmp_path):
    system = _agent(prompt_builder=_builder(tmp_path)).compose_system_prompt()
    assert "current_datetime:" in system
    assert "system_stats:" in system
    assert "open_app:" not in system
    assert "play_youtube:" not in system


def test_prompt_contains_the_local_date_and_time(tmp_path):
    agent = _agent(prompt_builder=_builder(tmp_path))
    system = agent.compose_system_prompt(now=_FROZEN)
    assert "It is 2:05 PM on Sunday, 15 November 2026 (UTC+05:00)" in system


def test_prompt_uses_the_honorific(tmp_path):
    with_title = _agent(prompt_builder=_builder(tmp_path), honorific="boss")
    assert 'Address the user as "boss"' in with_title.compose_system_prompt()
    without = _agent(prompt_builder=_builder(tmp_path), honorific="")
    assert "Address the user as" not in without.compose_system_prompt()


def test_honorific_defaults_to_the_jarvis_config(monkeypatch, tmp_path):
    config = JarvisConfig()
    config.jarvis.honorific = "ma'am"
    monkeypatch.setattr("openjarvis.core.config.load_config", lambda: config)
    agent = JarvisAgent(
        FakeEngine([{"content": "ok"}]),
        "m",
        tools=[],
        prompt_builder=_builder(tmp_path),
    )
    assert 'Address the user as "ma\'am"' in agent.compose_system_prompt()


def test_custom_system_prompt_replaces_the_default_persona(tmp_path):
    agent = _agent(prompt_builder=_builder(tmp_path), system_prompt="You are FRIDAY.")
    system = agent.compose_system_prompt()
    assert system.startswith("You are FRIDAY.")
    assert "You are JARVIS" not in system


def test_memory_and_profile_are_reread_every_turn(tmp_path):
    engine = FakeEngine([{"content": "Noted, sir."}])
    agent = _agent(engine, prompt_builder=_builder(tmp_path, user="- Name: Nova\n"))

    agent.run("hello")
    first = _system_text(engine.last_messages)
    assert "## User Profile" in first and "Name: Nova" in first
    assert "Lives in Rawalpindi" not in first

    user_md = tmp_path / "USER.md"
    user_md.write_text(
        user_md.read_text(encoding="utf-8") + "- Lives in Rawalpindi\n",
        encoding="utf-8",
    )
    agent.run("what's the weather?")
    assert "Lives in Rawalpindi" in _system_text(engine.last_messages)


def test_persona_can_be_disabled(tmp_path):
    agent = _agent(
        prompt_builder=_builder(tmp_path, user="- Name: Nova\n"),
        include_persona=False,
    )
    assert "Name: Nova" not in agent.compose_system_prompt()


def test_unreadable_persona_files_do_not_break_the_agent(tmp_path):
    class _Broken:
        def persona_sections(self) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    agent = _agent(prompt_builder=_Broken())
    assert "You are JARVIS" in agent.compose_system_prompt()


def test_caller_system_messages_are_folded_after_the_persona(tmp_path):
    agent = _agent(prompt_builder=_builder(tmp_path))
    context = AgentContext()
    context.conversation.add(Message(role=Role.SYSTEM, content="Memory: likes tea"))
    context.conversation.add(Message(role=Role.USER, content="hi"))
    context.conversation.add(Message(role=Role.ASSISTANT, content="Good evening."))
    messages = agent._build_messages("and you?", context)
    system = _system_text(messages)
    assert system.index("You are JARVIS") < system.index("Memory: likes tea")
    assert [m.role for m in messages[1:]] == [Role.USER, Role.ASSISTANT, Role.USER]


def test_shared_agent_does_not_accumulate_prompt_state(tmp_path):
    agent = _agent(prompt_builder=_builder(tmp_path))
    first = agent._build_messages("one")[0].content
    second = agent._build_messages("two")[0].content
    assert first.count("You are JARVIS") == second.count("You are JARVIS") == 1
    assert agent._system_prompt is None
    assert agent._prompt_builder is None


# ---------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------


def test_runs_the_tool_loop_and_speaks_the_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(pc_control, "_now", lambda: _FROZEN)
    engine = FakeEngine(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "current_datetime",
                        "arguments": json.dumps({"timezone": "UTC"}),
                    }
                ],
            },
            {"content": "It is five past nine in London, sir."},
        ]
    )
    agent = _agent(engine, prompt_builder=_builder(tmp_path))
    result = agent.run("What time is it in London?")
    assert result.content == "It is five past nine in London, sir."
    assert result.turns == 2
    assert [r.tool_name for r in result.tool_results] == ["current_datetime"]
    assert result.tool_results[0].success
    tool_messages = [m for m in engine.last_messages if m.role == Role.TOOL]
    assert "9:05 AM" in tool_messages[0].content


def test_think_tags_are_stripped_from_spoken_answers(tmp_path):
    engine = FakeEngine([{"content": "<think>plan</think>Right away, sir."}])
    result = _agent(engine, prompt_builder=_builder(tmp_path)).run("hi")
    assert result.content == "Right away, sir."


# ---------------------------------------------------------------------------
# Default tools
# ---------------------------------------------------------------------------


def test_default_toolkit_is_built_when_none_is_given(tmp_path):
    _reload_jarvis_tool_modules()
    agent = JarvisAgent(
        FakeEngine([{"content": "ok"}]),
        "m",
        prompt_builder=_builder(tmp_path),
        honorific="sir",
    )
    names = {tool.spec.name for tool in agent._tools}
    assert names == set(DEFAULT_JARVIS_TOOLS)


def test_explicit_empty_tool_list_is_respected(tmp_path):
    agent = JarvisAgent(
        FakeEngine([{"content": "ok"}]),
        "m",
        tools=[],
        prompt_builder=_builder(tmp_path),
        honorific="sir",
    )
    assert agent._tools == []
    assert "## Your tools" not in agent.compose_system_prompt()


def test_missing_tools_are_skipped():
    assert not ToolRegistry.contains("open_app")  # registry cleared by conftest
    assert build_jarvis_tools(["open_app"], config=JarvisConfig()) == []


def test_memory_tools_write_where_the_prompt_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    _reload_jarvis_tool_modules()
    config = JarvisConfig()
    config.memory_files.persona_name = "jarvis"
    tools = {
        tool.spec.name: tool
        for tool in build_jarvis_tools(
            ["memory_manage", "user_profile_manage"], config=config
        )
    }
    persona_dir = tmp_path / "personas" / "jarvis"
    assert tools["memory_manage"]._memory_path == persona_dir / "MEMORY.md"
    assert tools["user_profile_manage"]._user_path == persona_dir / "USER.md"


@pytest.mark.parametrize("name", ["../escape", "a/b"])
def test_invalid_persona_names_fall_back_to_default_paths(name):
    config = JarvisConfig()
    config.memory_files.persona_name = name
    assert jarvis_module._memory_file_paths(config) == (None, None)


def test_profile_pii_in_the_prompt_does_not_block_web_tools(tmp_path):
    """USER.md is always in the prompt; it must not taint every session."""
    from openjarvis.security.taint import TaintLabel, check_taint

    agent = _agent(
        prompt_builder=_builder(
            tmp_path, user="- Email: nova@example.com\n- Phone: +92 300 1234567\n"
        )
    )
    messages = agent._build_messages("open youtube.com")
    assert "nova@example.com" in _system_text(messages)
    taint = agent._executor._session_taint
    assert TaintLabel.PII not in taint.labels
    assert check_taint("open_website", taint) is None


def test_pii_the_user_says_still_gates_web_tools(tmp_path):
    from openjarvis.security.taint import TaintLabel, check_taint

    agent = _agent(prompt_builder=_builder(tmp_path))
    context = AgentContext()
    context.conversation.add(
        Message(role=Role.USER, content="my email is nova@example.com")
    )
    agent._build_messages("search the web for it", context)
    taint = agent._executor._session_taint
    assert TaintLabel.PII in taint.labels
    assert check_taint("open_website", taint) is not None
