"""ID helpers for Router backend runtime records."""

from __future__ import annotations

from uuid import uuid4


def prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def new_task_id() -> str:
    return prefixed_id("task")


def new_session_id() -> str:
    return prefixed_id("session")


def new_artifact_id() -> str:
    return prefixed_id("artifact")


def new_event_id() -> str:
    return prefixed_id("event")
