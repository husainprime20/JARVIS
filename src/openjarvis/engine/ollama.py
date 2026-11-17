"""Ollama inference engine backend."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, Dict, List

import httpx

from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message
from openjarvis.engine._base import (
    EngineConnectionError,
    InferenceEngine,
    estimate_prompt_tokens,
    messages_to_dicts,
)
from openjarvis.engine._http_async import (
    STREAM_TRANSPORT_ERRORS,
    AsyncHTTPEngineMixin,
)
from openjarvis.engine._stubs import StreamChunk

logger = logging.getLogger(__name__)

# Qwen3 treats ``/think`` and ``/no_think`` as soft-switch control tokens that
# toggle reasoning mode. Small models (e.g. qwen3:14b) fed a multi-line prompt
# sometimes emit one of these as the sole tool argument, e.g.
# ``{"command": "/no_think"}`` instead of the real command. Ollama parses that
# into a fully-formed tool_call via the model's chat template, so we have to
# drop it on our side before the agent executes garbage.
_QWEN_CONTROL_TOKENS = frozenset({"/think", "/no_think"})


def _is_control_token_only_args(raw_args: Any) -> bool:
    """Return True if tool-call arguments contain nothing but a Qwen3 token.

    ``raw_args`` may be a dict (Ollama's native shape) or a JSON / bare string.
    A call is considered degenerate only when it carries at least one control
    token and no other usable content, so legitimate calls such as
    ``{"command": "date"}`` or ``{"command": "echo /no_think"}`` are kept.
    """
    parsed: Any = raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            parsed = raw_args

    if isinstance(parsed, str):
        return parsed.strip().lower() in _QWEN_CONTROL_TOKENS

    if not isinstance(parsed, dict) or not parsed:
        return False

    saw_token = False
    for value in parsed.values():
        if not isinstance(value, str):
            return False  # a non-string value is real content
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.lower() in _QWEN_CONTROL_TOKENS:
            saw_token = True
        else:
            return False  # real string content
    return saw_token


def _default_num_ctx() -> int:
    """Default context window (tokens). Override with ``JARVIS_NUM_CTX``.

    Raised above Ollama's 4k default so an image (which costs many tokens)
    plus a real conversation fit. 16k is comfortable for small models on a
    typical consumer GPU.
    """
    try:
        return int(os.environ.get("JARVIS_NUM_CTX", "16384"))
    except ValueError:
        return 16384


# Ollama refuses to load a model when its weights plus the KV cache for the
# requested context do not fit in free memory: "model requires more system
# memory (3.1 GiB) than is available (2.4 GiB)". The cache grows with num_ctx,
# so a smaller context often fits where the 16k default does not, for example
# on an 8 GB PC with a browser open.
_LOW_MEMORY_MARKERS = (
    "requires more system memory",
    "out of memory",
    "insufficient memory",
    "not enough memory",
    "unable to allocate",
    "failed to allocate",
    "cannot allocate memory",
)

# The context is halved down to Ollama's own default, which still holds
# JARVIS's prompt (about 2.2k tokens of instructions and tool schemas) and a
# reply. Below it there would be no room left to answer.
_MIN_FALLBACK_NUM_CTX = 4096


def _is_low_memory_error(status_code: int, body: str) -> bool:
    """Whether Ollama could not load the model for lack of memory."""
    if status_code < 500:
        return False
    text = (body or "").lower()
    return any(marker in text for marker in _LOW_MEMORY_MARKERS)


def _ollama_error_text(body: str) -> str:
    """The ``error`` message of an Ollama error body, else the raw body."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return parsed["error"]
    return (body or "")[:500]


def _ollama_request_options(
    *,
    temperature: float,
    max_tokens: int,
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Build Ollama ``options`` dict from generate/stream kwargs."""
    options: Dict[str, Any] = {
        "temperature": temperature,
        "num_predict": max_tokens,
    }
    if kwargs.get("num_ctx") is not None:
        options["num_ctx"] = int(kwargs["num_ctx"])
    else:
        options["num_ctx"] = _default_num_ctx()
    if kwargs.get("num_gpu") is not None:
        options["num_gpu"] = int(kwargs["num_gpu"])
    return options


@EngineRegistry.register("ollama")
class OllamaEngine(AsyncHTTPEngineMixin, InferenceEngine):
    """Ollama backend via its native HTTP API."""

    engine_id = "ollama"

    # Ollama has no context-length overflow signal in its 400 bodies, so the
    # shared ``_raise_stream_http_error`` keeps its default (no
    # ``EngineContextLengthError`` branch, unlike the OpenAI-compat engines).

    _DEFAULT_HOST = "http://localhost:11434"

    def __init__(
        self,
        host: str | None = None,
        *,
        timeout: float = 1800.0,
    ) -> None:
        # Priority: explicit host (from config.toml) > OLLAMA_HOST env var > default
        if host is None:
            env_host = os.environ.get("OLLAMA_HOST")
            host = env_host or self._DEFAULT_HOST
        self._host = host.rstrip("/")
        # Used by the shared async streaming plumbing (AsyncHTTPEngineMixin) so a
        # wedged token read is bounded by ``timeout`` instead of hanging the
        # single event loop for the httpx default.
        self._timeout = timeout
        # Injection seam for tests: an ``httpx.MockTransport`` swapped in here drives
        # the async stream path with no real Ollama server. ``None`` in production so
        # httpx uses its default networking.
        self._async_transport: httpx.AsyncBaseTransport | None = None
        self._client = httpx.Client(base_url=self._host, timeout=timeout)
        # Last stream usage — captured from Ollama's final chunk
        self._last_stream_usage: Dict[str, int] = {}
        # Largest context known to fit in free memory, per model, learned from
        # Ollama's low-memory refusals (see ``_shrink_num_ctx``).
        self._num_ctx_cap: Dict[str, int] = {}

    def _cap_num_ctx(self, model: str, payload: Dict[str, Any], pinned: bool) -> None:
        """Start from the largest context already known to fit for *model*."""
        cap = self._num_ctx_cap.get(model)
        options = payload["options"]
        if not pinned and cap is not None and options.get("num_ctx", 0) > cap:
            options["num_ctx"] = cap

    def _shrink_num_ctx(
        self,
        model: str,
        payload: Dict[str, Any],
        pinned: bool,
        status_code: int,
        body: str,
    ) -> bool:
        """Halve the context after a low-memory refusal.

        Returns ``True`` when *payload* now asks for a smaller context worth
        retrying, ``False`` when the error is something else, the caller pinned
        ``num_ctx``, or the context is already at the floor. The smaller size
        is remembered so later requests do not fail first.
        """
        if pinned or not _is_low_memory_error(status_code, body):
            return False
        current = int(payload["options"].get("num_ctx") or _default_num_ctx())
        smaller = current // 2
        if smaller < _MIN_FALLBACK_NUM_CTX:
            return False
        payload["options"]["num_ctx"] = smaller
        self._num_ctx_cap[model] = smaller
        logger.warning(
            "Ollama cannot fit %s with a %d-token context in free memory (%s); "
            "retrying with %d tokens.",
            model,
            current,
            _ollama_error_text(body),
            smaller,
        )
        return True

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        msg_dicts = messages_to_dicts(messages)
        # Ollama expects tool_call arguments as dicts, not JSON strings
        for md in msg_dicts:
            for tc in md.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        pass
        payload: Dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "stream": False,
            "options": _ollama_request_options(
                temperature=temperature,
                max_tokens=max_tokens,
                kwargs=kwargs,
            ),
        }
        # Disable extended thinking by default (Qwen3.5 etc.).
        # When enabled, thinking tokens consume the entire budget and
        # the visible content comes back empty.
        if "think" not in kwargs:
            payload["think"] = False
        elif kwargs["think"] is not None:
            payload["think"] = kwargs["think"]
        # Pass tools if provided
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = tools

        # Apply structured output / JSON mode
        response_format = kwargs.get("response_format")
        if response_format is not None:
            from openjarvis.engine._stubs import ResponseFormat

            if isinstance(response_format, ResponseFormat):
                payload["format"] = "json"
            elif isinstance(response_format, dict):
                payload["format"] = "json"
        ctx_pinned = kwargs.get("num_ctx") is not None
        self._cap_num_ctx(model, payload, ctx_pinned)
        try:
            while True:
                resp = self._client.post("/api/chat", json=payload)
                if resp.status_code == 400 and payload.get("tools"):
                    # Model may not support function calling -- retry without tools
                    payload.pop("tools", None)
                    resp = self._client.post("/api/chat", json=payload)
                # Not enough free memory for this context: try a smaller one.
                if resp.is_server_error and self._shrink_num_ctx(
                    model, payload, ctx_pinned, resp.status_code, resp.text
                ):
                    continue
                break
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise EngineConnectionError(
                f"Ollama not reachable at {self._host}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            body = _ollama_error_text(exc.response.text) if exc.response else ""
            raise RuntimeError(
                f"Ollama returned {exc.response.status_code}: {body}"
            ) from exc
        data = resp.json()
        # prompt_eval_count = tokens actually evaluated (KV-cache-aware).
        # estimate_prompt_tokens = full prompt size (for cost comparison).
        # We report both so downstream can use the right one:
        #   prompt_tokens        → full size (what cloud would charge)
        #   prompt_tokens_evaluated → actual compute (with KV cache)
        reported_prompt = data.get("prompt_eval_count", 0)
        estimated_prompt = estimate_prompt_tokens(messages)
        prompt_tokens = max(reported_prompt, estimated_prompt)
        prompt_tokens_evaluated = (
            reported_prompt if reported_prompt > 0 else prompt_tokens
        )
        completion_tokens = data.get("eval_count", 0)
        content = data.get("message", {}).get("content", "")
        result: Dict[str, Any] = {
            "content": content,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "prompt_tokens_evaluated": prompt_tokens_evaluated,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": data.get("model", model),
            "finish_reason": "stop",
        }
        # Extract timing from Ollama response (nanoseconds → seconds)
        result["ttft"] = data.get("prompt_eval_duration", 0) / 1e9
        result["engine_timing"] = {
            k: data[k]
            for k in (
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
            )
            if k in data
        }
        # Extract tool calls if present
        raw_tool_calls = data.get("message", {}).get("tool_calls", [])
        if raw_tool_calls:
            tool_calls = []
            for i, tc in enumerate(raw_tool_calls):
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                if _is_control_token_only_args(raw_args):
                    logger.warning(
                        "Dropping Qwen3 control-token tool call %s(%r)",
                        fn.get("name", ""),
                        raw_args,
                    )
                    continue
                tool_calls.append(
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "name": fn.get("name", ""),
                        "arguments": (
                            json.dumps(raw_args)
                            if isinstance(raw_args, dict)
                            else raw_args
                        ),
                    }
                )
            if tool_calls:
                result["tool_calls"] = tool_calls
        return result

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages_to_dicts(messages),
            "stream": True,
            "options": _ollama_request_options(
                temperature=temperature,
                max_tokens=max_tokens,
                kwargs=kwargs,
            ),
        }
        # Mirror generate()'s default: disable extended thinking unless the
        # caller opted in. Qwen3/etc. with thinking on can stall the visible
        # stream for 60+ seconds before any tokens reach the client, which
        # frontends interpret as a "Load failed" timeout.
        if "think" not in kwargs:
            payload["think"] = False
        elif kwargs["think"] is not None:
            payload["think"] = kwargs["think"]
        ctx_pinned = kwargs.get("num_ctx") is not None
        self._cap_num_ctx(model, payload, ctx_pinned)
        try:
            # ASYNC streaming: ``httpx.AsyncClient`` + ``aiter_lines`` never
            # blocks the event loop between tokens (the previous SYNC
            # ``self._client`` + ``iter_lines`` inside this ``async def`` blocked
            # the single uvicorn worker on every inter-token wait, serializing all
            # concurrent chats and letting one wedged read freeze the whole API).
            # The shared client keeps pooled connections across turns.
            client = self._get_async_client()
            async with client.stream("POST", "/api/chat", json=payload) as resp:
                # ``not is_success`` covers 3xx as well as 4xx/5xx and maps
                # to ``EngineConnectionError`` (matching the OpenAI-compat
                # path) instead of leaking a raw ``httpx.HTTPStatusError``.
                # With redirects off (the default) an unexpected 3xx would
                # otherwise fall through to ``aiter_lines`` and surface as a
                # silent EMPTY stream rather than a clean engine error.
                if not resp.is_success:
                    # Read the (short) error body before touching ``.text``:
                    # a streaming response is otherwise unread.
                    await resp.aread()
                    if self._shrink_num_ctx(
                        model, payload, ctx_pinned, resp.status_code, resp.text
                    ):
                        # Nothing was yielded yet; start over with the smaller
                        # context, which is now this model's cap.
                        async for content in self.stream(
                            messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            **kwargs,
                        ):
                            yield content
                        return
                    self._raise_stream_http_error(resp.status_code, resp.text)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done", False):
                        reported_prompt = chunk.get("prompt_eval_count", 0)
                        est_prompt = estimate_prompt_tokens(messages)
                        full_prompt = max(reported_prompt, est_prompt)
                        evaluated = (
                            reported_prompt if reported_prompt > 0 else full_prompt
                        )
                        comp = chunk.get("eval_count", 0)
                        self._last_stream_usage = {
                            "prompt_tokens": full_prompt,
                            "prompt_tokens_evaluated": evaluated,
                            "completion_tokens": comp,
                            "total_tokens": full_prompt + comp,
                        }
                        break
        except STREAM_TRANSPORT_ERRORS as exc:
            # Transport failures (incl. a mid-stream server disconnect) map to a
            # clean error; the set is kept narrow (see STREAM_TRANSPORT_ERRORS)
            # so cancellation still propagates.
            raise EngineConnectionError(
                f"Ollama not reachable at {self._host}"
            ) from exc

    async def stream_full(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Yield ``StreamChunk``s including tool_calls.

        Unlike the default ``stream_full`` in the base class (which wraps
        ``stream()`` and drops tools), this posts to ``/api/chat`` with
        ``tools`` from kwargs and parses tool_calls out of the streamed
        response. Falls back to a tools-less retry on 400 (mirrors
        ``generate()``'s behaviour for models that don't support tools).
        """
        msg_dicts = messages_to_dicts(messages)
        for md in msg_dicts:
            for tc in md.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        pass

        payload: Dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "stream": True,
            "options": _ollama_request_options(
                temperature=temperature,
                max_tokens=max_tokens,
                kwargs=kwargs,
            ),
        }
        if "think" not in kwargs:
            payload["think"] = False
        elif kwargs["think"] is not None:
            payload["think"] = kwargs["think"]

        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = tools

        ctx_pinned = kwargs.get("num_ctx") is not None
        self._cap_num_ctx(model, payload, ctx_pinned)
        async for chunk in self._run_stream(
            payload,
            messages,
            retry_without_tools=bool(tools),
            ctx_pinned=ctx_pinned,
        ):
            yield chunk

    async def _run_stream(
        self,
        payload: Dict[str, Any],
        messages: Sequence[Message],
        *,
        retry_without_tools: bool,
        ctx_pinned: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        """Execute the streaming request and yield parsed StreamChunks."""
        try:
            # ASYNC streaming (see ``stream``): shared ``AsyncClient`` +
            # ``aiter_lines`` so rich streaming never stalls the event loop and
            # honours ``timeout``.
            client = self._get_async_client()
            async with client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code == 400 and retry_without_tools:
                    # Model doesn't support tools — retry without them.
                    # PRESERVED: this specific 400 path must still trigger the
                    # tools-less retry; only OTHER non-2xx responses map to
                    # EngineConnectionError below.
                    payload.pop("tools", None)
                    async for c in self._run_stream(
                        payload,
                        messages,
                        retry_without_tools=False,
                        ctx_pinned=ctx_pinned,
                    ):
                        yield c
                    return
                # ``not is_success`` covers 3xx as well as 4xx/5xx and maps
                # to ``EngineConnectionError`` (matching the OpenAI-compat
                # path) instead of leaking a raw ``httpx.HTTPStatusError``.
                # With redirects off (the default) an unexpected 3xx would
                # otherwise fall through to ``aiter_lines`` and surface as a
                # silent EMPTY stream rather than a clean engine error.
                if not resp.is_success:
                    await resp.aread()
                    if self._shrink_num_ctx(
                        payload["model"],
                        payload,
                        ctx_pinned,
                        resp.status_code,
                        resp.text,
                    ):
                        # Nothing was yielded yet; retry with the halved context.
                        async for c in self._run_stream(
                            payload,
                            messages,
                            retry_without_tools=retry_without_tools,
                            ctx_pinned=ctx_pinned,
                        ):
                            yield c
                        return
                    self._raise_stream_http_error(resp.status_code, resp.text)

                finish_reason: str | None = None
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = chunk.get("message", {}) or {}
                    content = message.get("content", "")
                    raw_tool_calls = message.get("tool_calls") or []

                    if content:
                        yield StreamChunk(content=content)

                    if raw_tool_calls:
                        # Ollama emits fully-formed tool_calls in a single
                        # chunk (not fragmented). Convert to the
                        # OpenAI-delta fragment shape that agent_manager_routes
                        # expects in _merge_tool_call_fragments.
                        fragments: List[Dict[str, Any]] = []
                        for tc in raw_tool_calls:
                            fn = tc.get("function", {}) or {}
                            raw_args = fn.get("arguments", "{}")
                            if _is_control_token_only_args(raw_args):
                                logger.warning(
                                    "Dropping Qwen3 control-token tool call %s(%r)",
                                    fn.get("name", ""),
                                    raw_args,
                                )
                                continue
                            args_str = (
                                json.dumps(raw_args)
                                if isinstance(raw_args, dict)
                                else str(raw_args)
                            )
                            i = len(fragments)
                            fragments.append(
                                {
                                    "index": i,
                                    "id": tc.get("id", f"call_{i}"),
                                    "type": "function",
                                    "function": {
                                        "name": fn.get("name", ""),
                                        "arguments": args_str,
                                    },
                                }
                            )
                        if fragments:
                            yield StreamChunk(tool_calls=fragments)
                            finish_reason = "tool_calls"

                    if chunk.get("done", False):
                        reported_prompt = chunk.get("prompt_eval_count", 0)
                        est_prompt = estimate_prompt_tokens(messages)
                        full_prompt = max(reported_prompt, est_prompt)
                        evaluated = (
                            reported_prompt if reported_prompt > 0 else full_prompt
                        )
                        comp = chunk.get("eval_count", 0)
                        self._last_stream_usage = {
                            "prompt_tokens": full_prompt,
                            "prompt_tokens_evaluated": evaluated,
                            "completion_tokens": comp,
                            "total_tokens": full_prompt + comp,
                        }
                        if finish_reason is None:
                            finish_reason = chunk.get("done_reason") or "stop"
                        yield StreamChunk(
                            finish_reason=finish_reason,
                            usage=dict(self._last_stream_usage),
                        )
                        break
        except STREAM_TRANSPORT_ERRORS as exc:
            # See ``stream``: transport failures (incl. a mid-stream server
            # disconnect) map to a clean error; the set is kept narrow so
            # cancellation still propagates.
            raise EngineConnectionError(
                f"Ollama not reachable at {self._host}"
            ) from exc

    def list_models(self) -> List[str]:
        try:
            resp = self._client.get("/api/tags")
            resp.raise_for_status()
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            logger.warning(
                "Failed to list models from Ollama at %s: %s",
                self._host,
                exc,
            )
            return []
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]

    def health(self) -> bool:
        try:
            resp = self._client.get("/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed at %s: %s", self._host, exc)
            return False

    def close(self) -> None:
        self._client.close()
        self._close_async_client()


__all__ = ["OllamaEngine"]
