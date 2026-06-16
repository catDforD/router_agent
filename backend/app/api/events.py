"""Task event streaming API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.database import get_session_factory
from app.core.errors import RepositoryNotFoundError
from app.services.event_service import (
    EventService,
    iter_event_stream,
    normalize_event_cursor,
)


router = APIRouter(tags=["events"])


@router.get("/api/tasks/{task_id}/events")
def stream_task_events(
    task_id: str,
    request: Request,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        cursor = normalize_event_cursor(
            after_seq=after_seq,
            last_event_id=last_event_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = request.app.state.settings
    session_factory = get_session_factory(settings)
    with session_factory() as session:
        try:
            EventService(session).ensure_task_exists(task_id)
        except RepositoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        iter_event_stream(session_factory, task_id, after_seq=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
