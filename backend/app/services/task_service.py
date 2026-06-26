"""Service boundary for frontend-facing task lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_artifact_id, new_event_id, new_session_id, new_task_id
from app.core.time import utc_now
from app.core.errors import RepositoryNotFoundError
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactType,
    ArtifactVisibility,
    AgentSession,
    AgentSessionStatus,
    CurrentArtifacts,
    DEFAULT_SCHEMA_VERSION,
    DifficultyProfile,
    DifficultySignals,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    GateState,
    ProjectContext,
    RouterEvent,
    RuntimeLimits,
    TaskPhase,
    TaskState,
    TaskStatus,
    TaskTrace,
    TaskType,
    WorkspaceContext,
)
from app.repositories._helpers import enum_value
from app.repositories.session_repo import AgentSessionRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService


CANCELLABLE_STATUSES = {
    TaskStatus.CREATED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_USER.value,
}
TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


class TaskMutationConflictError(Exception):
    """Raised when a task lifecycle mutation conflicts with current state."""


@dataclass(frozen=True)
class TaskCreateResult:
    task: TaskState
    raw_user_request_artifact_id: str


@dataclass(frozen=True)
class UserMessageResult:
    task: TaskState
    message_artifact_id: str


class TaskService:
    """Coordinates task state, artifact, and event writes for Task API flows."""

    def __init__(self, session: Session, artifact_root: Path) -> None:
        self.task_repository = TaskRepository(session)
        self.artifact_root = artifact_root
        self.artifact_store = ArtifactStore(session=session, artifact_root=artifact_root)
        self.event_service = EventService(session)

    def get_task(self, task_id: str) -> TaskState:
        return self.task_repository.get_task(task_id)

    def list_recent_tasks(
        self,
        *,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[TaskState]:
        return self.task_repository.list_recent_tasks(limit=limit, user_id=user_id)

    def delete_task(self, task_id: str) -> None:
        self.task_repository.get_task(task_id)
        self.task_repository.delete_task(task_id)
        task_artifact_dir = (self.artifact_root / task_id).resolve()
        artifact_root = self.artifact_root.resolve()
        try:
            task_artifact_dir.relative_to(artifact_root)
        except ValueError:
            return
        shutil.rmtree(task_artifact_dir, ignore_errors=True)

    def create_task(
        self,
        *,
        message: str,
        project_context: ProjectContext | dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> TaskCreateResult:
        now = utc_now()
        context = _project_context(project_context)
        task_session_id = session_id or new_session_id()
        task = self._build_initial_task_state(
            message=message,
            project_context=context,
            session_id=task_session_id,
            user_id=user_id,
            now=now,
        )
        self.task_repository.create_task(task)
        session_repository = AgentSessionRepository(self.task_repository.session)
        try:
            agent_session = session_repository.get_session(task_session_id)
        except RepositoryNotFoundError:
            agent_session = AgentSession(
                schema_version=DEFAULT_SCHEMA_VERSION,
                session_id=task_session_id,
                user_id=user_id,
                title=_task_title(message),
                status=AgentSessionStatus.ACTIVE,
                project_context=context,
                workspace=task.workspace,
                latest_task_id=task.task_id,
                latest_run_id=task.task_id,
                event_seq=0,
                runs=[],
                created_at=now,
                updated_at=now,
            )
            session_repository.create_session(agent_session)
        else:
            agent_session = agent_session.model_copy(
                update={
                    "latest_task_id": task.task_id,
                    "latest_run_id": task.task_id,
                    "updated_at": now,
                }
            )
            session_repository.update_session(agent_session)

        session_repository.create_run(
            run_id=task.task_id,
            session_id=task_session_id,
            task_id=task.task_id,
            status="created",
            user_message=message,
            created_at=now,
        )

        raw_artifact = self.artifact_store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type=ArtifactType.RAW_USER_REQUEST,
                version=1,
                name="raw_user_request.json",
                content={
                    "message": message,
                    "project_context": context.model_dump(mode="json"),
                    "created_at": now.isoformat(),
                },
                summary="Original user request.",
                visibility=ArtifactVisibility.USER,
                created_by=ArtifactCreator(type=ArtifactCreatorType.USER, id=user_id),
                metadata={
                    "target_plc_language": _optional_str(context.target_plc_language),
                    "target_platform": context.target_platform,
                    "tags": ["raw_user_request"],
                },
                artifact_id=new_artifact_id(),
                created_at=now,
                mime_type="application/json",
            )
        ).artifact

        self.event_service.append_event(
            self._build_event(
                task_id=task.task_id,
                event_type=EventType.TASK_CREATED,
                title="Task created",
                message="The task was created from the user request.",
                created_at=now,
                artifact_ids=[raw_artifact.artifact_id],
                payload={
                    "task_id": task.task_id,
                    "session_id": task_session_id,
                    "run_id": task.task_id,
                    "status": TaskStatus.CREATED.value,
                    "message": message,
                    "raw_user_request_artifact_id": raw_artifact.artifact_id,
                },
                source_id=user_id,
            )
        )
        return TaskCreateResult(
            task=self.task_repository.get_task(task.task_id),
            raw_user_request_artifact_id=raw_artifact.artifact_id,
        )

    def append_user_message(
        self,
        *,
        task_id: str,
        message: str,
        user_id: str | None = None,
    ) -> UserMessageResult:
        task = self.task_repository.get_task(task_id)

        now = utc_now()
        message_artifact = self.artifact_store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task_id,
                artifact_type=ArtifactType.MISC,
                version=1,
                name="user_message.json",
                content={
                    "message": message,
                    "created_at": now.isoformat(),
                },
                summary="User follow-up message.",
                visibility=ArtifactVisibility.USER,
                created_by=ArtifactCreator(type=ArtifactCreatorType.USER, id=user_id),
                metadata={"tags": ["user_message"]},
                artifact_id=new_artifact_id(),
                created_at=now,
                mime_type="application/json",
            )
        ).artifact

        self.event_service.append_event(
            self._build_event(
                task_id=task_id,
                event_type=EventType.TASK_UPDATED,
                title="User message added",
                message="A user follow-up message was added to the task.",
                created_at=now,
                artifact_ids=[message_artifact.artifact_id],
                payload={
                    "task_id": task_id,
                    "message_artifact_id": message_artifact.artifact_id,
                    "message": message,
                },
                source_id=user_id,
            )
        )
        persisted = self.task_repository.get_task(task_id)
        all_artifact_ids = list(persisted.current_artifacts.all_artifact_ids)
        if message_artifact.artifact_id not in all_artifact_ids:
            all_artifact_ids.append(message_artifact.artifact_id)
            persisted = self.task_repository.update_task_state(
                persisted.model_copy(
                    deep=True,
                    update={
                        "current_artifacts": persisted.current_artifacts.model_copy(
                            update={"all_artifact_ids": all_artifact_ids}
                        ),
                        "updated_at": now,
                    },
                )
            )

        return UserMessageResult(
            task=persisted,
            message_artifact_id=message_artifact.artifact_id,
        )

    def cancel_task(self, task_id: str, *, user_id: str | None = None) -> TaskState:
        task = self.task_repository.get_task_for_update(task_id)
        status = enum_value(task.status)
        if status == TaskStatus.CANCELLED.value:
            return task
        if status not in CANCELLABLE_STATUSES:
            raise TaskMutationConflictError(
                f"cannot cancel task in status {status!r}: {task_id}"
            )

        now = utc_now()
        cancelled = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.CANCELLED.value,
                "phase": TaskPhase.COMPLETED.value,
                "updated_at": now,
                "completed_at": now,
                "active_worker_jobs": [],
                "runtime_limits": task.runtime_limits.model_copy(
                    update={"active_parallel_workers": 0}
                ),
            }
        )
        self.task_repository.update_task_state(cancelled)
        self.event_service.append_event(
            self._build_event(
                task_id=task_id,
                event_type=EventType.TASK_CANCELLED,
                title="Task cancelled",
                message="The task was cancelled by the user.",
                created_at=now,
                artifact_ids=None,
                payload={
                    "task_id": task_id,
                    "status": TaskStatus.CANCELLED.value,
                },
                source_id=user_id,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
            )
        )
        return self.task_repository.get_task(task_id)

    def _build_initial_task_state(
        self,
        *,
        message: str,
        project_context: ProjectContext,
        session_id: str,
        user_id: str | None,
        now: datetime,
    ) -> TaskState:
        return TaskState(
            schema_version=DEFAULT_SCHEMA_VERSION,
            task_id=new_task_id(),
            session_id=session_id,
            user_id=user_id,
            title=_task_title(message),
            status=TaskStatus.CREATED,
            phase=TaskPhase.INTAKE,
            created_at=now,
            updated_at=now,
            raw_user_request=message,
            normalized_goal=None,
            task_type=TaskType.UNKNOWN,
            difficulty=DifficultyProfile(
                level="L0",
                score=0.0,
                confidence=0.1,
                reasons=["Task created before detailed classification."],
                signals=DifficultySignals(
                    has_existing_code=False,
                    has_io_points=False,
                    has_timing_logic=False,
                    has_state_machine=False,
                    has_safety_constraints=False,
                    has_emergency_stop=False,
                    has_interlock=False,
                    has_fault_latching=False,
                    has_mode_switching=False,
                    multi_module=False,
                    requirement_incomplete=False,
                ),
                requires_test=False,
                requires_formal=False,
                requires_repair_loop=False,
                need_clarification=False,
            ),
            project_context=project_context,
            workspace=(
                WorkspaceContext(
                    root=project_context.workspace_root,
                    current_directory=project_context.workspace_root,
                    writable=True,
                )
                if project_context.workspace_root is not None
                else None
            ),
            runtime_limits=RuntimeLimits(
                max_repair_rounds=3,
                repair_rounds=0,
                max_parallel_workers=4,
                active_parallel_workers=0,
                max_worker_calls=20,
                worker_calls_used=0,
                task_timeout_seconds=3600,
            ),
            gates=GateState(
                test_required=False,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                has_blocking_failure=False,
                can_finish_as_success=False,
            ),
            current_artifacts=CurrentArtifacts(all_artifact_ids=[]),
            active_worker_jobs=[],
            completed_worker_job_ids=[],
            assumptions=[],
            unresolved_questions=[],
            failures=[],
            trace=TaskTrace(main_agent_run_ids=[]),
            event_seq=0,
            metadata=None,
        )

    def _build_event(
        self,
        *,
        task_id: str,
        event_type: EventType,
        title: str,
        message: str,
        created_at: datetime,
        artifact_ids: list[str] | None,
        payload: dict[str, Any],
        source_id: str | None,
        openai_trace_id: str | None = None,
        main_agent_run_id: str | None = None,
    ) -> RouterEvent:
        return RouterEvent(
            schema_version=DEFAULT_SCHEMA_VERSION,
            event_id=new_event_id(),
            task_id=task_id,
            seq=0,
            type=event_type,
            source=EventSource(type=EventSourceType.FRONTEND, id=source_id),
            severity=EventSeverity.INFO,
            visibility=EventVisibility.USER,
            title=title,
            message=message,
            correlation=EventCorrelation(
                session_id=(
                    payload.get("session_id")
                    if isinstance(payload.get("session_id"), str)
                    else None
                ),
                run_id=(
                    payload.get("run_id")
                    if isinstance(payload.get("run_id"), str)
                    else None
                ),
                openai_trace_id=openai_trace_id,
                main_agent_run_id=main_agent_run_id,
                artifact_ids=artifact_ids,
            ),
            payload=payload,
            created_at=created_at,
        )


def _project_context(value: ProjectContext | dict[str, Any] | None) -> ProjectContext:
    if value is None:
        return ProjectContext()
    if isinstance(value, ProjectContext):
        return value
    return ProjectContext.model_validate(value)


def _task_title(message: str) -> str:
    compact = " ".join(message.split())
    if len(compact) <= 80:
        return compact
    return f"{compact[:77]}..."


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    return str(enum_value(value))
