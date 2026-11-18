"""The desktop app's cloud source: a Gemini (or other) key, Ollama off.

The Tauri shell (``frontend/src-tauri/src/lib.rs``) starts ``jarvis serve``
with arguments built in Rust, and the setup screens list providers in
TypeScript. These checks keep the three in step with the Python brains, and
with the Windows smoke test that runs the backend the way the app does.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB_RS = REPO / "frontend" / "src-tauri" / "src" / "lib.rs"
SETUP_TSX = REPO / "frontend" / "src" / "components" / "InferenceSourceSetup.tsx"


def _load_smoke():
    name = "smoke_jarvis_backend"
    path = REPO / "scripts" / "desktop" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke_mod = _load_smoke()


def _rust_cloud_providers() -> list[tuple[str, list[str]]]:
    """``(name, key_names)`` for each entry of lib.rs's ``CLOUD_PROVIDERS``."""
    source = LIB_RS.read_text(encoding="utf-8")
    block = re.search(
        r"const CLOUD_PROVIDERS: &\[CloudProvider\] = &\[(.*?)\n\];", source, re.S
    )
    assert block, "CLOUD_PROVIDERS not found in lib.rs"
    entries = re.findall(
        r'name: "([^"]+)",\s*label: "[^"]+",\s*key_names: &\[([^\]]*)\]',
        block.group(1),
    )
    return [(name, re.findall(r'"([^"]+)"', keys)) for name, keys in entries]


def _rust_test_args(test_name: str) -> list[str]:
    """The serve arguments a lib.rs unit test expects ``boot_plan`` to build."""
    source = LIB_RS.read_text(encoding="utf-8")
    body = re.search(rf"fn {test_name}\(\) \{{(.*?)\n    \}}\n", source, re.S)
    assert body, f"{test_name} not found in lib.rs"
    args = re.search(r"args_of\(&\[(.*?)\]\)", body.group(1), re.S)
    assert args, f"{test_name} has no args_of(&[...]) expectation"
    return re.findall(r'"([^"]+)"', args.group(1))


def test_rust_cloud_providers_are_jarvis_cloud_brains():
    from openjarvis.cli._jarvis_brain import BRAINS

    providers = _rust_cloud_providers()
    assert [name for name, _ in providers] == [
        "gemini",
        "openai",
        "anthropic",
        "openrouter",
    ]
    for name, key_names in providers:
        brain = BRAINS[name]
        assert brain.engine == "cloud", name
        # The app saves a setup key under the first name; the brain must read
        # it, and every name the app accepts must count for that brain.
        assert key_names and set(key_names) <= set(brain.env_vars), name


def test_rust_cloud_keys_are_managed_and_injected():
    """Keys the cloud source checks are ones the app loads into the server."""
    source = LIB_RS.read_text(encoding="utf-8")
    managed = re.search(
        r"const MANAGED_CLOUD_KEY_NAMES: &\[&str\] = &\[(.*?)\];", source, re.S
    )
    assert managed
    managed_names = set(re.findall(r'"([^"]+)"', managed.group(1)))
    for name, key_names in _rust_cloud_providers():
        assert set(key_names) <= managed_names, name


def test_setup_screens_offer_the_same_providers_as_the_app():
    source = SETUP_TSX.read_text(encoding="utf-8")
    block = re.search(
        r"export const CLOUD_PROVIDERS = \[(.*?)\] as const;", source, re.S
    )
    assert block
    ids = re.findall(r"id: '([^']+)'", block.group(1))
    assert ids == [name for name, _ in _rust_cloud_providers()]


def test_serve_accepts_the_arguments_the_app_builds():
    from openjarvis.cli.serve import serve

    params = {param.name: param for param in serve.params}
    assert "brain" in params
    for name, _ in _rust_cloud_providers():
        assert name in params["brain"].type.choices
    args = _rust_test_args("boot_plan_cloud_never_starts_ollama_or_downloads")
    assert args == ["--engine", "cloud", "--brain", "gemini", "--agent", "jarvis"]
    # The parser takes them as the app passes them (after --port).
    context = serve.make_context("serve", ["--port", "8000", *args])
    assert context.params["engine_key"] == "cloud"
    assert context.params["brain"] == "gemini"
    assert context.params["agent_name"] == "jarvis"


def test_smoke_test_runs_the_backend_like_the_cloud_source():
    args = _rust_test_args("boot_plan_cloud_never_starts_ollama_or_downloads")
    assert list(smoke_mod.CLOUD_SOURCE_ARGS) == args
    assert smoke_mod.GEMINI_MODEL == "gemini-3.5-flash-lite"


def test_cloud_source_serves_gemini_flash_lite_by_default():
    from openjarvis.cli._jarvis_brain import BRAINS

    assert BRAINS["gemini"].models[0] == smoke_mod.GEMINI_MODEL


@pytest.mark.parametrize("camel", [True, False])
def test_stand_in_gemini_calls_the_time_tool_then_answers(camel):
    declarations = "functionDeclarations" if camel else "function_declarations"
    body = {
        "contents": [{"role": "user", "parts": [{"text": "What time is it?"}]}],
        "tools": [{declarations: [{"name": "current_datetime"}]}],
    }
    reply = smoke_mod.gemini_reply(body)
    call = reply["candidates"][0]["content"]["parts"][0]["functionCall"]
    assert call == {"name": "current_datetime", "args": {"timezone": "Asia/Karachi"}}

    response_key = "functionResponse" if camel else "function_response"
    result = json.dumps({"summary": "It is noon (Asia/Karachi)."})
    body["contents"].append(
        {
            "role": "user",
            "parts": [
                {
                    response_key: {
                        "name": "current_datetime",
                        "response": {"result": result},
                    }
                }
            ],
        }
    )
    answer = smoke_mod.gemini_reply(body)["candidates"][0]["content"]["parts"][0]
    assert answer == {"text": "Certainly, sir. It is noon (Asia/Karachi)."}


def test_stand_in_gemini_only_calls_declared_tools():
    body = {"contents": [{"role": "user", "parts": [{"text": "What time is it?"}]}]}
    parts = smoke_mod.gemini_reply(body)["candidates"][0]["content"]["parts"]
    assert parts == [{"text": "At your service, sir."}]


def test_hud_explains_the_stand_ins_rejected_key():
    """The smoke test's refusal reads like Google's real one to the HUD."""
    assert "api key" in smoke_mod.GEMINI_KEY_ERROR.lower()
    conversation = (
        REPO / "frontend" / "src" / "lib" / "jarvis" / "conversation.ts"
    ).read_text(encoding="utf-8")
    assert "has('api key'" in conversation
