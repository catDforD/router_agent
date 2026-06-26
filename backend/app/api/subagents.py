"""Remote PLC subagent status API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.core.config import Settings
from app.mcp.subagent_client import SUBAGENT_AGENT_BY_WORKER
from app.models.router_schema import WorkerType


router = APIRouter(prefix="/api/subagents", tags=["subagents"])

SUBAGENT_PROBE_PATH = "/api/chat/stream"
SUBAGENT_PROBE_METHOD = "OPTIONS"
SUBAGENT_PROBE_SCOPE = "transport_reachability"
SUBAGENT_PROBE_TIMEOUT_SECONDS = 2.0


@router.get("/status")
def subagent_status(request: Request) -> dict[str, Any]:
    """Return Router's configured PLC subagent routes and HTTP reachability."""

    settings: Settings = request.app.state.settings
    worker_routes = {
        worker_type.value: _route_for_worker(settings, worker_type)
        for worker_type in WorkerType
    }
    uses_remote_subagent = any(route == "subagent" for route in worker_routes.values())
    probe = (
        probe_remote_subagent(settings)
        if uses_remote_subagent
        else {
            "status": "disabled",
            "online": None,
            "latency_ms": None,
            "status_code": None,
            "error": None,
            "scope": SUBAGENT_PROBE_SCOPE,
            "checked_at": _now_iso(),
        }
    )

    workers = []
    for worker_type in WorkerType:
        worker = worker_type.value
        route = worker_routes[worker]
        status = probe["status"] if route == "subagent" else route
        online = probe["online"] if route == "subagent" else None
        error = probe["error"] if route == "subagent" else None
        latency_ms = probe["latency_ms"] if route == "subagent" else None
        status_code = probe["status_code"] if route == "subagent" else None
        probe_scope = (
            probe.get("scope", SUBAGENT_PROBE_SCOPE)
            if route == "subagent"
            else None
        )
        workers.append(
            {
                "worker_type": worker,
                "agent_id": SUBAGENT_AGENT_BY_WORKER[worker],
                "route": route,
                "status": status,
                "online": online,
                "latency_ms": latency_ms,
                "status_code": status_code,
                "error": error,
                "probe_scope": probe_scope,
            }
        )

    return {
        "mode": settings.mcp_mode,
        "base_url": settings.subagent_api_base_url,
        "probe": {
            "method": SUBAGENT_PROBE_METHOD,
            "path": SUBAGENT_PROBE_PATH,
            "scope": SUBAGENT_PROBE_SCOPE,
            **probe,
        },
        "workers": workers,
    }


def probe_remote_subagent(settings: Settings) -> dict[str, Any]:
    """Check whether the configured remote HTTP subagent API is reachable."""

    url = _join_url(settings.subagent_api_base_url, SUBAGENT_PROBE_PATH)
    headers = {"Accept": "application/json"}
    if settings.subagent_api_token:
        headers["Authorization"] = f"Bearer {settings.subagent_api_token}"

    started = perf_counter()
    try:
        response = httpx.request(
            SUBAGENT_PROBE_METHOD,
            url,
            headers=headers,
            timeout=SUBAGENT_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return _probe_failure(started, "timeout", "Remote subagent probe timed out")
    except httpx.HTTPError as exc:
        return _probe_failure(
            started,
            "offline",
            f"Remote subagent probe failed: {type(exc).__name__}",
        )

    latency_ms = _elapsed_ms(started)
    online = response.status_code < 500 and response.status_code != 404
    if online:
        status = "online"
        error = None
    else:
        status = "unavailable"
        error = f"Remote subagent returned HTTP {response.status_code}"

    return {
        "status": status,
        "online": online,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
        "error": error,
        "scope": SUBAGENT_PROBE_SCOPE,
        "checked_at": _now_iso(),
    }


def _route_for_worker(settings: Settings, worker_type: WorkerType) -> str:
    worker_modes = {
        WorkerType.PLC_DEV: settings.plc_dev_mode,
        WorkerType.PLC_TEST: settings.plc_test_mode,
        WorkerType.PLC_FORMAL: settings.plc_formal_mode,
        WorkerType.PLC_REPAIR: settings.plc_repair_mode,
    }
    override = worker_modes[worker_type]
    if override is not None:
        return override
    if settings.mcp_mode in {"real", "subagent"}:
        return settings.mcp_mode
    return "mock"


def _probe_failure(started: float, status: str, error: str) -> dict[str, Any]:
    return {
        "status": status,
        "online": False,
        "latency_ms": _elapsed_ms(started),
        "status_code": None,
        "error": error,
        "scope": SUBAGENT_PROBE_SCOPE,
        "checked_at": _now_iso(),
    }


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
