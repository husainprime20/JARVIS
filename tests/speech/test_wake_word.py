"""Tests for transcript-based wake-word spotting (speech/wake_word.py)."""

from __future__ import annotations

import pytest

from openjarvis.speech.wake_word import (
    WakeWordDetector,
    is_cancel_command,
    is_noise_transcript,
    is_stop_command,
)


@pytest.fixture
def detector() -> WakeWordDetector:
    return WakeWordDetector("jarvis")


@pytest.mark.parametrize(
    ("heard", "command"),
    [
        ("Jarvis, open Spotify.", "open Spotify"),
        ("jarvis turn the volume up", "turn the volume up"),
        ("Hey Jarvis! Open github.com please.", "Open github.com"),
        ("What's the time, Jarvis?", "What's the time"),
        ("um, jarvis, uh, what's 25 times 4?", "what's 25 times 4"),
        ("Please Jarvis, lock the screen", "lock the screen"),
        (
            "Okay so Jarvis what's the weather in Rawalpindi",
            "what's the weather in Rawalpindi",
        ),
        ("Jervis, play Despacito on YouTube", "play Despacito on YouTube"),
        ("Hey service, play some music", "play some music"),
        ("JARVIS: remind me who I am", "remind me who I am"),
    ],
)
def test_extracts_the_request_and_keeps_its_wording(detector, heard, command):
    match = detector.find(heard)
    assert match is not None
    assert match.command == command
    assert match.heard == heard


@pytest.mark.parametrize("heard", ["Jarvis?", "Jarvis.", "Hey, jarvis", "Hey Jarvis!"])
def test_name_alone_wakes_with_an_empty_command(detector, heard):
    match = detector.find(heard)
    assert match is not None
    assert match.command == ""


@pytest.mark.parametrize(
    "heard",
    [
        "",
        "   ",
        "Open Spotify",
        "I was telling my friend about Jarvis yesterday and he laughed",
        "The customer service was terrible",
        "jarvisville is a town",  # not a whole word
        "Thank you.",
    ],
)
def test_ignores_speech_that_does_not_address_jarvis(detector, heard):
    assert detector.find(heard) is None


def test_custom_wake_word_and_variants():
    friday = WakeWordDetector("Friday", extra_variants=["fry day"])
    assert friday.find("Friday, play music").command == "play music"
    assert friday.find("fry day, what time is it").command == "what time is it"
    assert friday.find("Jarvis, play music") is None
    assert friday.wake_word == "friday"


def test_only_the_leading_wake_word_is_removed(detector):
    match = detector.find("Jarvis, what's my name? Jarvis is cool.")
    assert match.command == "what's my name? Jarvis is cool"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Goodbye, Jarvis.", True),
        ("Jarvis, stop listening", True),
        ("go to sleep", True),
        ("goodbye my friend", False),
        ("open Spotify", False),
    ],
)
def test_stop_commands(text, expected):
    assert is_stop_command(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Never mind.", True), ("cancel", True), ("cancel my 3pm meeting", False)],
)
def test_cancel_commands(text, expected):
    assert is_cancel_command(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", True),
        (".", True),
        ("Thank you.", True),
        ("Thanks for watching!", True),
        ("open Spotify", False),
        ("thank you jarvis", False),
    ],
)
def test_noise_transcripts(text, expected):
    assert is_noise_transcript(text) is expected
