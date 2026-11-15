"""MEMORY.md / USER.md must be read and written as UTF-8 on every platform.

``SystemPromptBuilder`` reads persona files as UTF-8. Without an explicit
encoding, Windows writes them in the ANSI code page (cp1252), so a remembered
non-ASCII fact made every later prompt build fail.
"""

from __future__ import annotations

import pathlib

import pytest

from openjarvis.tools.memory_manage import MemoryManageTool
from openjarvis.tools.user_profile_manage import UserProfileManageTool

_FACT = "Name: Nova (نووا), likes café au lait"


@pytest.fixture
def recorded_encodings(monkeypatch):
    encodings: list = []
    real_read, real_write = pathlib.Path.read_text, pathlib.Path.write_text

    def _read(self, *args, **kwargs):
        encodings.append(kwargs.get("encoding", args[0] if args else None))
        return real_read(self, *args, **kwargs)

    def _write(self, data, *args, **kwargs):
        encodings.append(kwargs.get("encoding", args[0] if args else None))
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", _read)
    monkeypatch.setattr(pathlib.Path, "write_text", _write)
    return encodings


@pytest.mark.parametrize(
    ("factory", "filename"),
    [
        (lambda path: MemoryManageTool(memory_path=path), "MEMORY.md"),
        (lambda path: UserProfileManageTool(user_path=path), "USER.md"),
    ],
)
def test_non_ascii_facts_round_trip_as_utf8(
    tmp_path, recorded_encodings, factory, filename
):
    path = tmp_path / filename
    tool = factory(path)

    assert tool.execute(action="add", entry=_FACT).success
    assert tool.execute(action="update", entry="café au lait", new_entry="chai").success
    read = tool.execute(action="read")

    assert "نووا" in read.content and "chai" in read.content
    assert "نووا" in path.read_bytes().decode("utf-8")
    assert tool.execute(action="remove", entry="Nova").success
    assert recorded_encodings and set(recorded_encodings) == {"utf-8"}
