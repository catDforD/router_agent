"""Frontend-facing AgentSession API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db_session, get_session_factory
from app.core.errors import (
    ArtifactStoreConflictError,
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from app.models.router_schema import AgentSession, ProjectContext, TaskState
from app.services.event_service import (
    EventService,
    iter_session_event_stream,
    normalize_event_cursor,
)
from app.services.runtime_service import run_runtime_start_task
from app.services.session_service import AgentSessionConflictError, AgentSessionService


router = APIRouter(tags=["sessions"])


class CreateSessionRequest(BaseModel):
    message: str = Field(min_length=1)
    project_context: ProjectContext = Field(default_factory=ProjectContext)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AppendSessionMessageRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class SessionResponse(BaseModel):
    session: AgentSession
    latest_task: TaskState | None = None


class CreateSessionResponse(BaseModel):
    session: AgentSession
    task: TaskState
    task_id: str
    run_id: str
    events_url: str


class AppendSessionMessageResponse(BaseModel):
    session: AgentSession
    task: TaskState
    task_id: str
    run_id: str


class ListSessionsResponse(BaseModel):
    sessions: list[AgentSession]


def get_request_db_session(request: Request) -> Iterator[Session]:
    yield from get_db_session(request.app.state.settings)


def get_agent_session_service(
    request: Request,
    session: Session = Depends(get_request_db_session),
) -> AgentSessionService:
    return AgentSessionService(
        session=session,
        artifact_root=request.app.state.settings.artifact_root,
    )


@router.post(
    "/api/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    body: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_request_db_session),
    service: AgentSessionService = Depends(get_agent_session_service),
) -> CreateSessionResponse:
    try:
        result = service.create_session(
            message=body.message,
            project_context=body.project_context,
        )
        session.commit()
    except (RepositoryConflictError, ArtifactStoreConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise

    _schedule_runtime_start(
        background_tasks,
        result.task.task_id,
        settings=request.app.state.settings,
    )
    return CreateSessionResponse(
        session=result.session,
        task=result.task,
        task_id=result.task.task_id,
        run_id=result.task.task_id,
        events_url=f"/api/sessions/{result.session.session_id}/events",
    )


@router.get("/api/sessions", response_model=ListSessionsResponse)
def list_sessions(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    service: AgentSessionService = Depends(get_agent_session_service),
) -> ListSessionsResponse:
    return ListSessionsResponse(sessions=service.list_sessions(limit=limit))


@router.get("/api/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    service: AgentSessionService = Depends(get_agent_session_service),
) -> SessionResponse:
    try:
        agent_session = service.get_session(session_id)
        latest_task = service.get_latest_task(session_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SessionResponse(session=agent_session, latest_task=latest_task)


@router.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    session: Session = Depends(get_request_db_session),
    service: AgentSessionService = Depends(get_agent_session_service),
) -> None:
    try:
        service.delete_session(session_id)
        session.commit()
    except RepositoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise


@router.post(
    "/api/sessions/{session_id}/messages",
    response_model=AppendSessionMessageResponse,
)
def append_session_message(
    session_id: str,
    body: AppendSessionMessageRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_request_db_session),
    service: AgentSessionService = Depends(get_agent_session_service),
) -> AppendSessionMessageResponse:
    try:
        result = service.append_message(session_id=session_id, message=body.message)
        session.commit()
    except RepositoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentSessionConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RepositoryConflictError, ArtifactStoreConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise

    _schedule_runtime_start(
        background_tasks,
        result.task_id,
        settings=request.app.state.settings,
    )
    return AppendSessionMessageResponse(
        session=result.session,
        task=result.task,
        task_id=result.task_id,
        run_id=result.run_id,
    )


@router.get("/api/sessions/{session_id}/events")
def stream_session_events(
    session_id: str,
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
    with session_factory() as db_session:
        try:
            EventService(db_session).ensure_session_exists(session_id)
        except RepositoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        iter_session_event_stream(session_factory, session_id, after_seq=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _schedule_runtime_start(
    background_tasks: BackgroundTasks,
    task_id: str,
    *,
    settings: Settings,
) -> None:
    background_tasks.add_task(run_runtime_start_task, task_id, settings)
