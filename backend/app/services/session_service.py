"""Frontend-facing AgentSession service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import RepositoryNotFoundError
from app.models.router_schema import (
    AgentSession,
    AgentSessionStatus,
    ProjectContext,
    TaskState,
)
from app.repositories.session_repo import AgentSessionRepository
from app.repositories.task_repo import TaskRepository
from app.services.task_service import TaskCreateResult, TaskService


class AgentSessionConflictError(Exception):
    """Raised when a session cannot accept a new message."""


@dataclass(frozen=True)
class AgentSessionCreateResult:
    session: AgentSession
    task: TaskState
    raw_user_request_artifact_id: str


@dataclass(frozen=True)
class AgentSessionMessageResult:
    session: AgentSession
    task: TaskState
    task_id: str
    run_id: str


class AgentSessionService:
    """Coordinates session-level conversation operations."""

    def __init__(self, session: Session, artifact_root: Path) -> None:
        self.session = session
        self.artifact_root = artifact_root
        self.session_repository = AgentSessionRepository(session)
        self.task_repository = TaskRepository(session)
        self.task_service = TaskService(session=session, artifact_root=artifact_root)

    def create_session(
        self,
        *,
        message: str,
        project_context: ProjectContext | dict | None = None,
        user_id: str | None = None,
    ) -> AgentSessionCreateResult:
        created = self.task_service.create_task(
            message=message,
            project_context=project_context,
            user_id=user_id,
        )
        agent_session = self.session_repository.get_session(created.task.session_id)
        return AgentSessionCreateResult(
            session=agent_session,
            task=created.task,
            raw_user_request_artifact_id=created.raw_user_request_artifact_id,
        )

    def append_message(
        self,
        *,
        session_id: str,
        message: str,
        user_id: str | None = None,
    ) -> AgentSessionMessageResult:
        agent_session = self.session_repository.get_session(session_id)
        if agent_session.status != AgentSessionStatus.ACTIVE.value:
            raise AgentSessionConflictError(
                f"cannot append message to session in status {agent_session.status!r}: "
                f"{session_id}"
            )

        created: TaskCreateResult = self.task_service.create_task(
            message=message,
            project_context=agent_session.project_context,
            session_id=session_id,
            user_id=user_id,
        )
        updated_session = self.session_repository.get_session(session_id)
        return AgentSessionMessageResult(
            session=updated_session,
            task=created.task,
            task_id=created.task.task_id,
            run_id=created.task.task_id,
        )

    def get_session(self, session_id: str) -> AgentSession:
        return self.session_repository.get_session(session_id)

    def list_sessions(self, *, limit: int = 50) -> list[AgentSession]:
        return self.session_repository.list_sessions(limit=limit)

    def delete_session(self, session_id: str) -> None:
        task_ids = [
            task.task_id
            for task in self.task_repository.list_tasks_by_session(session_id)
        ]
        self.session_repository.delete_session(session_id)
        for task_id in task_ids:
            self.task_service.delete_task(task_id)

    def get_latest_task(self, session_id: str) -> TaskState | None:
        agent_session = self.session_repository.get_session(session_id)
        if agent_session.latest_task_id is None:
            return None
        try:
            return self.task_repository.get_task(agent_session.latest_task_id)
        except RepositoryNotFoundError:
            return None
