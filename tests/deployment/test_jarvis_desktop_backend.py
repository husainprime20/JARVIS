"""The backend the JARVIS Windows installer bundles (scripts/desktop/)."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = REPO / "scripts" / "desktop" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage_mod = _load("stage_jarvis_backend")
smoke_mod = _load("smoke_jarvis_backend")

WHEEL = "openjarvis_rust-0.1.0-cp312-cp312-win_amd64.whl"


def _fake_wheel(directory: Path) -> Path:
    path = directory / WHEEL
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "openjarvis_rust-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openjarvis-rust\nVersion: 0.1.0\n",
        )
    return path


def test_wheel_python_version():
    assert stage_mod.wheel_python_version(WHEEL) == (3, 12)
    assert stage_mod.wheel_python_version("x-1.0-cp310-cp310-linux_x86_64.whl") == (
        3,
        10,
    )
    with pytest.raises(stage_mod.StagingError):
        stage_mod.wheel_python_version("x-1.0-py3-none-any.whl")


def test_pyproject_points_at_the_wheel_and_pins_python():
    original = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    text = stage_mod.point_rust_source_at_wheel(original, f"wheels/{WHEEL}")
    text = stage_mod.pin_python(text, 3, 12)
    assert f'openjarvis-rust = {{ path = "wheels/{WHEEL}" }}' in text
    assert 'requires-python = ">=3.12, <3.13"' in text
    assert "rust/crates/openjarvis-python" not in text
    # Nothing else changes.
    assert len(text.splitlines()) == len(original.splitlines())


def test_rewrites_fail_loudly_when_the_entry_is_missing():
    with pytest.raises(stage_mod.StagingError):
        stage_mod.point_rust_source_at_wheel("[project]\n", "wheels/x.whl")
    with pytest.raises(stage_mod.StagingError):
        stage_mod.pin_python("[project]\n", 3, 12)


def test_stage_builds_a_self_contained_backend(tmp_path):
    out = stage_mod.stage(wheel=_fake_wheel(tmp_path), out=tmp_path / "b", lock=False)

    assert (out / "pyproject.toml").is_file() and (out / "uv.lock").is_file()
    assert (out / "src/openjarvis/agents/jarvis.py").is_file()
    assert (out / "configs/openjarvis/examples/jarvis.toml").is_file()
    assert (out / "wheels" / WHEEL).is_file()
    assert (out / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    info = json.loads((out / "JARVIS_BACKEND.json").read_text(encoding="utf-8"))
    assert info["wheel"] == WHEEL
    # Paths hatchling force-includes must exist or the editable build fails.
    assert (out / "scripts/install").is_dir() and (out / "deploy/windows").is_dir()
    # The prebuilt wheel replaces the Rust sources; the app keys off this to
    # skip its Rust toolchain check. Tests, docs and the UI stay out.
    for excluded in ("rust", "tests", "docs", "frontend", ".github"):
        assert not (out / excluded).exists(), excluded


def test_stand_in_model_drives_the_jarvis_tools():
    tools = [
        {"function": {"name": name}}
        for name in ("current_datetime", "user_profile_manage", "open_app")
    ]

    def first_call(text: str):
        reply = smoke_mod.scripted_reply(
            {"messages": [{"role": "user", "content": text}], "tools": tools}
        )
        return reply["tool_calls"][0]["function"]

    assert first_call("what time is it")["name"] == "current_datetime"
    assert first_call("remember my name")["arguments"]["entry"] == "Name: Nova"
    assert first_call("Jarvis, open notepad")["arguments"] == {"name": "notepad"}
    answer = smoke_mod.scripted_reply(
        {
            "messages": [
                {"role": "user", "content": "x"},
                {"role": "tool", "content": json.dumps({"summary": "It is noon."})},
            ]
        }
    )
    assert answer["content"] == "Certainly, sir. It is noon."


def test_smoke_upload_is_valid_multipart_for_the_transcribe_route():
    """The smoke test builds its upload by hand; FastAPI must parse it."""
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/upload")
    async def upload(
        file: UploadFile = File(...), prompt: str = Form(""), vad: str = Form("")
    ):
        return {
            "name": file.filename,
            "size": len(await file.read()),
            "prompt": prompt,
            "vad": vad,
        }

    body, content_type = smoke_mod.multipart(
        {"prompt": "Jarvis.", "vad": "1"}, "utterance.wav", b"RIFF" + b"\0" * 60
    )
    response = TestClient(app).post(
        "/upload", content=body, headers={"Content-Type": content_type}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "name": "utterance.wav",
        "size": 64,
        "prompt": "Jarvis.",
        "vad": "1",
    }
