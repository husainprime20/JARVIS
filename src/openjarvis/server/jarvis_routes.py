"""HUD support for JARVIS mode: live system stats and who is answering."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

jarvis_router = APIRouter(prefix="/v1/jarvis", tags=["jarvis"])


def _settings(request: Request) -> Dict[str, Any]:
    config = getattr(request.app.state, "config", None)
    if config is None:
        from openjarvis.core.config import load_config

        config = load_config()
    jarvis_cfg = config.jarvis
    speech = config.speech
    return {
        "wake_word": (jarvis_cfg.wake_word or "jarvis").strip(),
        "honorific": (jarvis_cfg.honorific or "").strip(),
        "follow_up_seconds": float(jarvis_cfg.follow_up_seconds),
        "require_wake_word": bool(jarvis_cfg.require_wake_word),
        "voice": f"{speech.tts_backend} / {speech.voice_id or 'default'}",
    }


def _tool_names(agent: Any) -> List[str]:
    tools = getattr(agent, "_tools", None) or []
    names: List[str] = []
    for tool in tools:
        spec = getattr(tool, "spec", None)
        if spec is not None and getattr(spec, "name", ""):
            names.append(spec.name)
    return names


@jarvis_router.get("/status")
async def jarvis_status(request: Request) -> Dict[str, Any]:
    """Everything the HUD shows: brain, settings and a system-stats snapshot."""
    from openjarvis.tools.pc_control import collect_system_stats

    state = request.app.state
    agent = getattr(state, "agent", None)
    agent_id = getattr(agent, "agent_id", None) if agent is not None else None
    if agent_id is None:
        agent_id = getattr(state, "agent_name", None)

    try:
        # cpu_percent() samples for a moment; keep it off the event loop.
        stats: Dict[str, Any] = await asyncio.to_thread(collect_system_stats)
    except Exception as exc:  # report it; the HUD shows "stats unavailable"
        logger.warning("System stats unavailable: %s", exc)
        stats = {"error": str(exc)}

    return {
        "agent": agent_id,
        "jarvis_mode": agent_id == "jarvis",
        "model": getattr(state, "model", "") or "",
        "engine": getattr(state, "engine_name", "") or "",
        "tools": _tool_names(agent),
        **_settings(request),
        "stats": stats,
    }


__all__ = ["jarvis_router"]
