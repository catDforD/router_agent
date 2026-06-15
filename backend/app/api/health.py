"""Health check API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.config import Settings


router = APIRouter(tags=["health"])


def health_payload(settings: Settings) -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
    }


@router.get("/health")
def root_health(request: Request) -> dict[str, Any]:
    return health_payload(request.app.state.settings)


@router.get("/api/health")
def api_health(request: Request) -> dict[str, Any]:
    return health_payload(request.app.state.settings)
