"""Tests for ``jarvis listen`` (cli/listen_cmd.py)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from openjarvis.cli import listen_cmd
from openjarvis.cli.listen_cmd import (
    VoiceLoop,
    _transcribe,
    friendly_error,
    greeting,
    speakable,
)
from openjarvis.core.types import Role
from openjarvis.speech.wake_word import WakeWordDetector
from tests.agents.fake_engine import FakeEngine

# ---------------------------------------------------------------------------
# VoiceLoop with scripted transcripts and a fake clock
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class _Script:
    """Feeds transcripts; ``None`` simulates silence until the wait expires."""

    def __init__(self, clock, items):
        self.clock = clock
        self.items = list(items)
        self.waits: list = []

    def __call__(self, wait):
        self.waits.append(wait)
        item = self.items.pop(0) if self.items else "goodbye jarvis"
        if item is None and wait is not None:
            self.clock.now += wait
        return item


def _loop(items, *, replies=None, **kwargs):
    clock = _Clock()
    hear = _Script(clock, items)
    said, shown, asked = [], [], []
    replies = list(replies or [])

    def ask(request, history):
        asked.append((request, [(m.role, m.content) for m in history]))
        reply = replies.pop(0) if replies else f"Done: {request}"
        if isinstance(reply, Exception):
            raise reply
        return reply

    loop = VoiceLoop(
        hear=hear,
        ask=ask,
        say=said.append,
        show=lambda kind, text: shown.append((kind, text)),
        detector=WakeWordDetector("jarvis"),
        clock=clock,
        **kwargs,
    )
    return loop, SimpleNamespace(
        hear=hear, said=said, shown=shown, asked=asked, clock=clock
    )


def test_only_requests_addressed_to_jarvis_are_answered():
    loop, io = _loop(
        ["what a lovely day", "Jarvis, open Spotify", None, "and play music"]
    )
    loop.run(max_rounds=4)
    assert [request for request, _ in io.asked] == ["open Spotify"]
    assert ("overheard", "what a lovely day") in io.shown
    assert ("overheard", "and play music") in io.shown


def test_follow_up_needs_no_wake_word_until_the_window_closes():
    loop, io = _loop(["Jarvis, open Spotify", "play some jazz", None, "turn it up"])
    loop.run(max_rounds=4)
    assert [r for r, _ in io.asked] == ["open Spotify", "play some jazz"]
    # waits: standby (None), follow-up window, follow-up window, standby again
    assert io.hear.waits[0] is None
    assert io.hear.waits[1] == pytest.approx(8.0)
    assert io.hear.waits[3] is None


def test_history_is_passed_to_follow_ups():
    loop, io = _loop(["Jarvis, my name is Nova", "what's my name?"])
    loop.run(max_rounds=2)
    _, history = io.asked[1]
    assert history == [
        (Role.USER, "my name is Nova"),
        (Role.ASSISTANT, "Done: my name is Nova"),
    ]


def test_history_is_capped():
    loop, io = _loop([f"Jarvis, request {i}" for i in range(10)], max_history=4)
    loop.run(max_rounds=10)
    assert len(loop.history) == 4
    assert loop.history[-1].content == "Done: request 9"


def test_name_alone_acknowledges_and_opens_the_window():
    loop, io = _loop(["Jarvis?", "what's the weather"])
    loop.run(max_rounds=2)
    assert io.said[0] == "Yes, sir?"
    assert [r for r, _ in io.asked] == ["what's the weather"]


def test_acknowledgements_vary_and_respect_the_honorific():
    loop, io = _loop(["Jarvis", None, "Jarvis", None, "Jarvis"], honorific="")
    loop.run(max_rounds=5)
    assert io.said == ["Yes?", "At your service.", "I'm listening."]


def test_goodbye_stops_the_loop():
    loop, io = _loop(["Jarvis, goodbye", "Jarvis, open Spotify"])
    loop.run(max_rounds=5)
    assert io.asked == []
    assert io.said == ["Very good, sir. Signing off."]
    assert ("jarvis", "Very good, sir. Signing off.") in io.shown


def test_never_mind_cancels_the_follow_up_window():
    loop, io = _loop(["Jarvis", "never mind", "open Spotify"])
    loop.run(max_rounds=3)
    assert io.said[-1] == "Very well, sir."
    assert io.asked == []


def test_noise_is_ignored_even_when_awake():
    loop, io = _loop(["Jarvis", "Thank you.", "", "open Spotify"])
    loop.run(max_rounds=4)
    assert [r for r, _ in io.asked] == ["open Spotify"]


def test_without_wake_word_everything_is_a_request():
    loop, io = _loop(["open Spotify", "Jarvis, play jazz"], require_wake_word=False)
    loop.run(max_rounds=2)
    assert [r for r, _ in io.asked] == ["open Spotify", "play jazz"]
    assert io.hear.waits == [None, None]


def test_engine_errors_are_spoken_kindly_and_the_loop_continues():
    loop, io = _loop(
        ["Jarvis, open Spotify", "Jarvis, again"],
        replies=[RuntimeError("429 RESOURCE_EXHAUSTED: quota"), "Spotify is open."],
    )
    loop.run(max_rounds=2)
    assert "usage limit" in io.said[0]
    assert any(kind == "error" and "429" in text for kind, text in io.shown)
    assert io.said[1] == "Spotify is open."
    assert len(loop.history) == 2  # failed request not remembered


def test_empty_reply_still_confirms():
    loop, io = _loop(["Jarvis, lock the screen"], replies=[""])
    loop.run(max_rounds=1)
    assert io.said == ["Done, sir."]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_speakable_strips_markdown_and_links():
    text = (
        "## Weather\n\n"
        "- **Rawalpindi**: 24°C and *sunny*\n"
        "- See [the forecast](https://example.com/f) or https://weather.example\n"
        "```python\nprint('hi')\n```"
    )
    spoken = speakable(text)
    assert spoken == (
        "Weather. Rawalpindi: 24°C and sunny. See the forecast or the link on "
        "screen. (code shown on screen)"
    )


def test_speakable_trims_long_answers_at_a_sentence():
    text = " ".join(f"Sentence number {i} is here." for i in range(100))
    spoken = speakable(text, limit=120)
    assert spoken.endswith("The rest is on screen.")
    assert len(spoken) < 170
    assert "Sentence number 3 is here." in spoken


def test_speakable_drops_think_blocks():
    assert speakable("<think>secret plan</think>Right away.") == "Right away."


@pytest.mark.parametrize(
    ("error", "phrase"),
    [
        ("Error 429: Too Many Requests", "usage limit"),
        ("RESOURCE_EXHAUSTED quota exceeded", "usage limit"),
        ("401 Unauthorized: API key not valid", "API key was rejected"),
        ("Ollama not reachable: connection refused", "can't reach my brain"),
        ("KeyError: 'x'", "something went wrong"),
    ],
)
def test_friendly_error(error, phrase):
    assert phrase in friendly_error(RuntimeError(error))


def test_friendly_error_honorific():
    assert friendly_error(RuntimeError("boom"), "boss").startswith(
        "I'm afraid something went wrong, boss."
    )


@pytest.mark.parametrize(
    ("hour", "start"),
    [
        (8, "Good morning"),
        (14, "Good afternoon"),
        (21, "Good evening"),
        (2, "Good evening"),
    ],
)
def test_greeting(hour, start):
    assert greeting("sir", datetime(2026, 11, 15, hour)).startswith(f"{start}, sir.")
    assert greeting("", datetime(2026, 11, 15, hour)).startswith(f"{start}.")


def test_transcribe_uses_prompt_and_vad_only_when_supported():
    class Rich:
        def transcribe(self, audio, *, format, language, prompt=None, vad_filter=False):
            self.kwargs = dict(
                format=format, language=language, prompt=prompt, vad_filter=vad_filter
            )
            return SimpleNamespace(text="  Jarvis, hello  ")

    class Plain:
        def transcribe(self, audio, *, format="wav", language=None):
            self.kwargs = dict(format=format, language=language)
            return SimpleNamespace(text="hi")

    rich, plain = Rich(), Plain()
    assert _transcribe(rich, b"x", language="en", prompt="Jarvis.") == "Jarvis, hello"
    assert rich.kwargs == {
        "format": "wav",
        "language": "en",
        "prompt": "Jarvis.",
        "vad_filter": True,
    }
    assert _transcribe(plain, b"x", language=None, prompt="Jarvis.") == "hi"
    assert plain.kwargs == {"format": "wav", "language": None}


# ---------------------------------------------------------------------------
# The command, end to end in text mode
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_brain(monkeypatch):
    """Route ``select_brain`` to a scripted engine."""
    from openjarvis.cli import _jarvis_brain

    engine = FakeEngine([{"content": "Good evening, sir. Spotify is open."}])
    choice = _jarvis_brain.BrainChoice(
        "gemini", "Google Gemini", "cloud", engine, "gemini-3.5-flash-lite"
    )
    monkeypatch.setattr(_jarvis_brain, "select_brain", lambda *a, **k: choice)
    return engine


def test_text_mode_session(fake_brain, monkeypatch):
    result = CliRunner().invoke(
        listen_cmd.listen,
        ["--text", "--mute", "--tools", "calculator"],
        input="Jarvis, open Spotify\ngoodbye\n",
    )
    assert result.exit_code == 0, result.output
    assert "Google Gemini · gemini-3.5-flash-lite" in result.output
    assert "Spotify is open." in result.output
    assert "Signing off." in result.output
    system = fake_brain.last_messages[0]
    assert system.role == Role.SYSTEM and "You are JARVIS" in system.content
    assert fake_brain.last_messages[-1].content == "open Spotify"


def test_end_of_input_signs_off(fake_brain):
    result = CliRunner().invoke(listen_cmd.listen, ["--text", "--mute"], input="")
    assert result.exit_code == 0, result.output
    assert "Signing off." in result.output


def test_no_brain_prints_setup_help_and_fails(monkeypatch):
    from openjarvis.cli import _jarvis_brain

    def unavailable(*args, **kwargs):
        raise _jarvis_brain.BrainUnavailableError("Get a key at aistudio")

    monkeypatch.setattr(_jarvis_brain, "select_brain", unavailable)
    result = CliRunner().invoke(listen_cmd.listen, ["--text", "--mute"], input="")
    assert result.exit_code == 1
    assert "Get a key at aistudio" in result.output


def test_check_lists_brains_and_voice(monkeypatch):
    from openjarvis.cli import _jarvis_brain

    monkeypatch.setattr(
        _jarvis_brain,
        "detect_brains",
        lambda config: [
            (_jarvis_brain.BRAINS["gemini"], "gemini-3.5-flash-lite", "ready"),
            (_jarvis_brain.BRAINS["ollama"], None, "not running"),
        ],
    )
    result = CliRunner().invoke(listen_cmd.listen, ["--check"])
    assert result.exit_code == 0, result.output
    assert "Google Gemini: gemini-3.5-flash-lite" in result.output
    assert "Ollama (offline): not running" in result.output
    assert "Wake word" in result.output
    assert "To fix this" not in result.output  # a brain is ready


def test_check_explains_how_to_fix_what_is_missing(monkeypatch):
    from openjarvis.cli import _jarvis_brain

    monkeypatch.setattr(
        _jarvis_brain,
        "detect_brains",
        lambda config: [(_jarvis_brain.BRAINS["ollama"], None, "not running")],
    )
    monkeypatch.setattr(listen_cmd.importlib.util, "find_spec", lambda name: None)
    result = CliRunner().invoke(listen_cmd.listen, ["--check"])
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())  # undo Rich line wrapping
    assert "aistudio.google.com" in output
    assert "uv sync --extra jarvis --group desktop-native" in output


def test_listen_is_a_registered_command():
    from openjarvis.cli import cli

    assert "listen" in cli.commands
