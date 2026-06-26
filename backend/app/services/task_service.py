"""Service boundary for frontend-facing task lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_event_id, new_session_id, new_task_id
from app.core.time import utc_now
from app.core.errors import RepositoryNotFoundError
from app.models.router_schema import (
    AgentSession,
    AgentSessionStatus,
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
from app.repositories._helpers import sanitize_legacy_agent_session_payload
from app.repositories.session_repo import AgentSessionRepository
from app.repositories.task_repo import TaskRepository
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
    raw_user_request_path: str


@dataclass(frozen=True)
class UserMessageResult:
    task: TaskState
    message_path: str


class TaskService:
    """Coordinates task state, workspace files, and event writes for Task API flows."""

    def __init__(
        self,
        session: Session,
        artifact_root: Path,
        session_workspace_root: Path | None = None,
    ) -> None:
        self.task_repository = TaskRepository(session)
        self.artifact_root = artifact_root
        self.session_workspace_root = session_workspace_root or (
            artifact_root.parent / "workspaces"
        )
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
        task = self.task_repository.get_task(task_id)
        self.task_repository.delete_task(task_id)
        task_artifact_dir = (self.artifact_root / task_id).resolve()
        artifact_root = self.artifact_root.resolve()
        try:
            task_artifact_dir.relative_to(artifact_root)
        except ValueError:
            pass
        else:
            shutil.rmtree(task_artifact_dir, ignore_errors=True)
        if task.workspace is not None and task.workspace.writable:
            workspace_root = Path(task.workspace.root).expanduser().resolve()
            session_root = self.session_workspace_root.expanduser().resolve()
            try:
                workspace_root.relative_to(session_root)
            except ValueError:
                return
            shutil.rmtree(workspace_root, ignore_errors=True)

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

        raw_request_path = self._write_workspace_json(
            task,
            path=f".router/requests/{task.task_id}_raw_user_request.json",
            payload={
                "message": message,
                "project_context": context.model_dump(mode="json"),
                "created_at": now.isoformat(),
            },
        )
        task = self._record_workspace_file(
            task.task_id,
            raw_request_path,
            role="raw_user_request",
        )

        self.event_service.append_event(
            self._build_event(
                task_id=task.task_id,
                event_type=EventType.TASK_CREATED,
                title="Task created",
                message="The task was created from the user request.",
                created_at=now,
                artifact_ids=None,
                payload={
                    "task_id": task.task_id,
                    "session_id": task_session_id,
                    "run_id": task.task_id,
                    "status": TaskStatus.CREATED.value,
                    "message": message,
                    "raw_user_request_path": raw_request_path,
                },
                source_id=user_id,
            )
        )
        return TaskCreateResult(
            task=self.task_repository.get_task(task.task_id),
            raw_user_request_path=raw_request_path,
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
        message_path = self._write_workspace_json(
            task,
            path=f".router/requests/{task_id}_{int(now.timestamp() * 1000)}_user_message.json",
            payload={
                "message": message,
                "created_at": now.isoformat(),
            },
        )
        persisted = self._record_workspace_file(task_id, message_path)

        self.event_service.append_event(
            self._build_event(
                task_id=task_id,
                event_type=EventType.TASK_UPDATED,
                title="User message added",
                message="A user follow-up message was added to the task.",
                created_at=now,
                artifact_ids=None,
                payload={
                    "task_id": task_id,
                    "message_path": message_path,
                    "message": message,
                },
                source_id=user_id,
            )
        )
        return UserMessageResult(
            task=persisted,
            message_path=message_path,
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
        workspace_root = _workspace_root(
            project_context=project_context,
            session_id=session_id,
            session_workspace_root=self.session_workspace_root,
        )
        workspace_root.mkdir(parents=True, exist_ok=True)
        (workspace_root / ".router" / "runs").mkdir(parents=True, exist_ok=True)

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
            workspace=WorkspaceContext(
                root=str(workspace_root),
                current_directory=str(workspace_root),
                writable=project_context.workspace_root is None,
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
            active_worker_jobs=[],
            completed_worker_job_ids=[],
            assumptions=[],
            unresolved_questions=[],
            failures=[],
            trace=TaskTrace(main_agent_run_ids=[]),
            event_seq=0,
            metadata=None,
        )

    def _write_workspace_json(
        self,
        task: TaskState,
        *,
        path: str,
        payload: dict[str, Any],
    ) -> str:
        if task.workspace is None:
            raise RuntimeError("task workspace is required for workspace file writes")
        workspace_root = Path(task.workspace.root)
        target = workspace_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return target.relative_to(workspace_root).as_posix()

    def _record_workspace_file(
        self,
        task_id: str,
        path: str,
        *,
        role: str | None = None,
    ) -> TaskState:
        task = self.task_repository.get_task(task_id)
        all_paths = list(task.current_files.all_paths)
        if path not in all_paths:
            all_paths.append(path)
        updates: dict[str, Any] = {"all_paths": all_paths}
        if role is not None:
            updates[role] = path
        return self.task_repository.update_task_state(
            task.model_copy(
                deep=True,
                update={
                    "current_files": task.current_files.model_copy(update=updates),
                    "updated_at": utc_now(),
                },
            )
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
    payload = sanitize_legacy_agent_session_payload({"project_context": dict(value)})
    return ProjectContext.model_validate(payload["project_context"])


def _workspace_root(
    *,
    project_context: ProjectContext,
    session_id: str,
    session_workspace_root: Path,
) -> Path:
    if project_context.workspace_root is not None:
        return Path(project_context.workspace_root).expanduser().resolve()
    return (session_workspace_root / session_id).expanduser().resolve()


def _task_title(message: str) -> str:
    compact = " ".join(message.split())
    if len(compact) <= 80:
        return compact
    return f"{compact[:77]}..."


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    return str(enum_value(value))
