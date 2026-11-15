"""Tests for the always-listening recorder helpers in speech/voice_io.py."""

from __future__ import annotations

import io
import struct
import sys
import threading
import types
import wave

import pytest

from openjarvis.speech import voice_io

LOUD = struct.pack("<h", 4000) * voice_io._CHUNK
QUIET = struct.pack("<h", 20) * voice_io._CHUNK
CHUNKS_PER_SECOND = voice_io._SAMPLE_RATE / voice_io._CHUNK  # ~15.6


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n):
        self.reads += 1
        data = self._chunks.pop(0) if self._chunks else QUIET
        return data, False


@pytest.fixture
def mic(monkeypatch):
    """Install a fake ``sounddevice`` whose stream plays scripted chunks."""
    state: dict = {}

    def feed(chunks):
        state["stream"] = _FakeStream(chunks)
        return state["stream"]

    module = types.ModuleType("sounddevice")
    module.RawInputStream = lambda **kwargs: state["stream"]
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return feed


def _seconds(n: float) -> int:
    return int(n * CHUNKS_PER_SECOND)


def _frames(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav)) as wf:
        assert wf.getframerate() == voice_io._SAMPLE_RATE
        return wf.getnframes()


def test_returns_the_utterance_with_pre_roll(mic):
    mic([QUIET] * 20 + [LOUD] * 10 + [QUIET] * 30)
    wav = voice_io.record_utterance(silence_threshold=500, silence_seconds=0.5)
    assert wav is not None
    chunks = _frames(wav) // voice_io._CHUNK
    pre_roll = _seconds(0.4)
    # pre-roll + speech + trailing silence that ended the utterance
    assert chunks == pre_roll + 10 + _seconds(0.5)


def test_short_clicks_are_not_speech(mic):
    mic([QUIET] * 5 + [LOUD] + [QUIET] * 40)
    assert voice_io.record_utterance(silence_threshold=500, silence_seconds=0.5) is None


def test_gives_up_after_wait_seconds(mic):
    stream = mic([QUIET] * 200)
    assert voice_io.record_utterance(silence_threshold=500, wait_seconds=1.0) is None
    assert stream.reads == _seconds(1.0)


def test_max_seconds_caps_long_speech(mic):
    mic([LOUD] * 400)
    wav = voice_io.record_utterance(silence_threshold=500, max_seconds=2.0)
    assert _frames(wav) // voice_io._CHUNK == _seconds(2.0)


def test_stop_event_ends_the_wait(mic):
    mic([QUIET] * 50)
    stop = threading.Event()
    stop.set()
    assert voice_io.record_utterance(stop_event=stop) is None


def test_noise_floor_is_the_median_level(mic):
    mic([QUIET] * 10 + [LOUD] * 3)
    floor = voice_io.measure_noise_floor(seconds=_seconds(1.0) / CHUNKS_PER_SECOND)
    assert floor == pytest.approx(20.0)


def test_missing_sounddevice_explains_how_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(RuntimeError, match="pip install sounddevice"):
        voice_io.record_utterance(wait_seconds=0.1)
