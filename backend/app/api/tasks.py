"""Frontend-facing task lifecycle API endpoints."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.errors import (
    ArtifactStoreConflictError,
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from app.models.router_schema import ProjectContext, TaskState
from app.services.runtime_service import run_runtime_resume_task, run_runtime_start_task
from app.services.task_service import (
    TaskMutationConflictError,
    TaskService,
    UserMessageResult,
)


router = APIRouter(tags=["tasks"])


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


@router.get("/api/tasks/{task_id}", response_model=TaskState)
def get_task(
    task_id: str,
    service: TaskService = Depends(get_task_service),
) -> TaskState:
    try:
        return service.get_task(task_id)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
