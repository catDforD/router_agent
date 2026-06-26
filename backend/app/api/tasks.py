"""Frontend-facing task lifecycle API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
import logging

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.errors import (
    ArtifactStoreConflictError,
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from app.core.logging import log_with_context
from app.models.router_schema import ProjectContext, TaskState, TaskStatus
from app.repositories._helpers import enum_value
from app.services.runtime_service import (
    run_runtime_followup_task,
    run_runtime_resume_task,
    run_runtime_start_task,
)
from app.services.task_service import (
    TaskMutationConflictError,
    TaskService,
    UserMessageResult,
)
from app.services.trace_summary import TaskTraceSummary, TraceSummaryService


router = APIRouter(tags=["tasks"])
LOGGER = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class CreateTaskRequest(BaseModel):
    message: str = Field(min_length=1)
    project_context: ProjectContext = Field(default_factory=ProjectContext)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AppendUserMessageRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str
    events_url: str


class TaskListResponse(BaseModel):
    tasks: list[TaskState]


class UserMessageResponse(BaseModel):
    task: TaskState
    message_artifact_id: str


def get_request_db_session(request: Request) -> Iterator[Session]:
    yield from get_db_session(request.app.state.settings)


def get_task_service(
    request: Request,
    session: Session = Depends(get_request_db_session),
) -> TaskService:
    return TaskService(
        session=session,
        artifact_root=request.app.state.settings.artifact_root,
    )


@router.post(
    "/api/tasks",
    response_model=CreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    body: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_request_db_session),
    service: TaskService = Depends(get_task_service),
) -> CreateTaskResponse:
    try:
        result = service.create_task(
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
    return CreateTaskResponse(
        task_id=result.task.task_id,
        status=result.task.status,
        events_url=f"/api/tasks/{result.task.task_id}/events",
    )


@router.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    service: TaskService = Depends(get_task_service),
) -> TaskListResponse:
    return TaskListResponse(tasks=service.list_recent_tasks(limit=limit))


@router.get("/api/tasks/{task_id}", response_model=TaskState)
def get_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> TaskState:
    try:
        return service.get_task(task_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: str,
    session: Session = Depends(get_request_db_session),
    service: TaskService = Depends(get_task_service),
) -> None:
    try:
        service.delete_task(task_id)
        session.commit()
    except RepositoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/api/tasks/{task_id}/trace", response_model=TaskTraceSummary)
def get_task_trace(
    task_id: str,
    session: Session = Depends(get_request_db_session),
) -> TaskTraceSummary:
    try:
        return TraceSummaryService(session).get_task_trace_summary(task_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log_with_context(
            LOGGER,
            logging.ERROR,
            "Trace summary projection failed",
            task_id=task_id,
            exception_type=type(exc).__name__,
        )
        raise


@router.post("/api/tasks/{task_id}/messages", response_model=UserMessageResponse)
def append_user_message(
    task_id: str,
    body: AppendUserMessageRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_request_db_session),
    service: TaskService = Depends(get_task_service),
) -> UserMessageResponse:
    try:
        result = service.append_user_message(task_id=task_id, message=body.message)
        session.commit()
    except RepositoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskMutationConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RepositoryConflictError, ArtifactStoreConflictError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise

    if enum_value(result.task.status) in TERMINAL_TASK_STATUSES:
        _schedule_runtime_followup(
            background_tasks,
            task_id,
            result.message_artifact_id,
            settings=request.app.state.settings,
        )
    else:
        _schedule_runtime_resume(
            background_tasks,
            task_id,
            settings=request.app.state.settings,
        )
    return _user_message_response(result)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskState)
def cancel_task(
    task_id: str,
    session: Session = Depends(get_request_db_session),
    service: TaskService = Depends(get_task_service),
) -> TaskState:
    try:
        task = service.cancel_task(task_id)
        session.commit()
    except RepositoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskMutationConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise

    return task


def _user_message_response(result: UserMessageResult) -> UserMessageResponse:
    return UserMessageResponse(
        task=result.task,
        message_artifact_id=result.message_artifact_id,
    )


def _schedule_runtime_start(
    background_tasks: BackgroundTasks,
    task_id: str,
    *,
    settings: Settings,
) -> None:
    background_tasks.add_task(run_runtime_start_task, task_id, settings)


def _schedule_runtime_resume(
    background_tasks: BackgroundTasks,
    task_id: str,
    *,
    settings: Settings,
) -> None:
    background_tasks.add_task(run_runtime_resume_task, task_id, settings)


def _schedule_runtime_followup(
    background_tasks: BackgroundTasks,
    task_id: str,
    message_artifact_id: str,
    *,
    settings: Settings,
) -> None:
    background_tasks.add_task(
        run_runtime_followup_task,
        task_id,
        message_artifact_id,
        settings,
    )
