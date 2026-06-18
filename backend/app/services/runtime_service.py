"""Background Runtime Loop service for Main Agent task execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import (
    MainAgentRunner,
    MainAgentService,
    build_task_event,
)
from app.agents.output_schema import MainAgentEpisodeOutput
from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.errors import RepositoryNotFoundError
from app.core.ids import new_event_id, prefixed_id
from app.core.time import utc_now
from app.models.db_models import TaskRow
from app.models.router_schema import (
    Artifact,
    ClarificationQuestion,
    ClarificationStatus,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    RouterEvent,
    TaskPhase,
    TaskState,
    TaskStatus,
)
from app.repositories._helpers import enum_value
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactStore
from app.services.event_service import EventService


DEFAULT_RUNTIME_LEASE_SECONDS = 300
RUNTIME_METADATA_KEY = "runtime"
RUNTIME_STATUS_RUNNING = "running"
RUNTIME_STATUS_IDLE = "idle"
RUNTIME_STATUS_ERROR = "error"
USER_MESSAGE_TAG = "user_message"
ANSWER_MAX_CHARS = 2_000
TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


@dataclass(frozen=True)
class RuntimeLeaseClaim:
    """Result of attempting to claim a task for one Runtime episode."""

    task_id: str
    claimed: bool
    task: TaskState | None = None
    episode_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RuntimeRunResult:
    """Internal result returned by RuntimeService start/resume operations."""

    task_id: str
    status: str
    reason: str | None = None
    episode_id: str | None = None
    output: MainAgentEpisodeOutput | None = None


class RuntimeService:
    """Owns background Runtime execution sessions and episode leases."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_factory: sessionmaker[Session] | None = None,
        artifact_root: Path | None = None,
        mcp_mode: str | None = None,
        mock_scenario: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
        runner: MainAgentRunner | None = None,
        lease_seconds: int = DEFAULT_RUNTIME_LEASE_SECONDS,
        lease_owner: str = "in-process",
    ) -> None:
        app_settings = settings or get_settings()
        self.settings = app_settings
        self.session_factory = session_factory or get_session_factory(app_settings)
        self.artifact_root = artifact_root or app_settings.artifact_root
        self.mcp_mode = mcp_mode or app_settings.mcp_mode
        self.mock_scenario = mock_scenario or app_settings.mock_scenario
        self.model = model if model is not None else app_settings.main_agent_model
        self.max_turns = max_turns or app_settings.main_agent_max_turns
        self.runner = runner
        self.lease_seconds = lease_seconds
        self.lease_owner = lease_owner

    def start_task(self, task_id: str) -> RuntimeRunResult:
        """Start background Runtime execution for a newly created task."""

        return self.run_main_agent_episode(task_id, allow_waiting_user=False)

    def run_main_agent_episode(
        self,
        task_id: str,
        *,
        allow_waiting_user: bool = False,
    ) -> RuntimeRunResult:
        """Claim and run one Main Agent episode for a runnable task."""

        with self.session_factory() as session:
            claim = self.claim_runtime_episode(
                session,
                task_id,
                allow_waiting_user=allow_waiting_user,
            )
            if not claim.claimed:
                session.rollback()
                return RuntimeRunResult(
                    task_id=task_id,
                    status="skipped",
                    reason=claim.reason,
                )

            self._checkpoint_session(session)
            episode_id = claim.episode_id
            try:
                output = self._run_claimed_episode(
                    session,
                    task_id=task_id,
                )
                self._checkpoint_session(session)
                latest = TaskRepository(session).get_task(task_id)
                self.release_runtime_episode(
                    session,
                    task_id=task_id,
                    episode_id=episode_id,
                    status=RUNTIME_STATUS_IDLE,
                )
                self._checkpoint_session(session)
                return RuntimeRunResult(
                    task_id=task_id,
                    status=_runtime_result_status(latest),
                    episode_id=episode_id,
                    output=output,
                )
            except Exception as exc:
                session.rollback()
                self.record_runtime_exception(
                    task_id=task_id,
                    episode_id=episode_id,
                    exc=exc,
                )
                return RuntimeRunResult(
                    task_id=task_id,
                    status="error",
                    reason=str(exc),
                    episode_id=episode_id,
                )

    def resume_after_user_message(self, task_id: str) -> RuntimeRunResult:
        """Resume a waiting task after the frontend records a user message."""

        with self.session_factory() as session:
            claim = self.claim_runtime_episode(
                session,
                task_id,
                allow_waiting_user=True,
                require_waiting_user=True,
            )
            if not claim.claimed:
                session.rollback()
                return RuntimeRunResult(
                    task_id=task_id,
                    status="skipped",
                    reason=claim.reason,
                )

            self._checkpoint_session(session)
            episode_id = claim.episode_id
            try:
                resume_result = self._apply_latest_user_message(session, task_id)
                if resume_result is not None:
                    self._checkpoint_session(session)

                latest = TaskRepository(session).get_task(task_id)
                if not _is_runnable(latest, allow_waiting_user=False):
                    self.release_runtime_episode(
                        session,
                        task_id=task_id,
                        episode_id=episode_id,
                        status=RUNTIME_STATUS_IDLE,
                    )
                    self._checkpoint_session(session)
                    return RuntimeRunResult(
                        task_id=task_id,
                        status="skipped",
                        reason="task_not_runnable_after_resume",
                        episode_id=episode_id,
                    )

                output = self._run_claimed_episode(session, task_id=task_id)
                self._checkpoint_session(session)
                latest = TaskRepository(session).get_task(task_id)
                self.release_runtime_episode(
                    session,
                    task_id=task_id,
                    episode_id=episode_id,
                    status=RUNTIME_STATUS_IDLE,
                )
                self._checkpoint_session(session)
                return RuntimeRunResult(
                    task_id=task_id,
                    status=_runtime_result_status(latest),
                    episode_id=episode_id,
                    output=output,
                )
            except Exception as exc:
                session.rollback()
                self.record_runtime_exception(
                    task_id=task_id,
                    episode_id=episode_id,
                    exc=exc,
                )
                return RuntimeRunResult(
                    task_id=task_id,
                    status="error",
                    reason=str(exc),
                    episode_id=episode_id,
                )

    def claim_runtime_episode(
        self,
        session: Session,
        task_id: str,
        *,
        allow_waiting_user: bool = False,
        require_waiting_user: bool = False,
        now: datetime | None = None,
    ) -> RuntimeLeaseClaim:
        """Claim a task row for Runtime execution if it is runnable."""

        current_time = now or utc_now()
        try:
            task = _get_locked_task(session, task_id)
        except RepositoryNotFoundError:
            return RuntimeLeaseClaim(
                task_id=task_id,
                claimed=False,
                reason="task_not_found",
            )

        if _is_terminal(task):
            return RuntimeLeaseClaim(
                task_id=task_id,
                claimed=False,
                task=task,
                reason="terminal_task",
            )

        if require_waiting_user and enum_value(task.status) != TaskStatus.WAITING_USER.value:
            return RuntimeLeaseClaim(
                task_id=task_id,
                claimed=False,
                task=task,
                reason="task_not_waiting_user",
            )

        if not _is_runnable(task, allow_waiting_user=allow_waiting_user):
            return RuntimeLeaseClaim(
                task_id=task_id,
                claimed=False,
                task=task,
                reason="waiting_for_user",
            )

        runtime_metadata = _runtime_metadata(task)
        if _lease_is_active(runtime_metadata, current_time):
            return RuntimeLeaseClaim(
                task_id=task_id,
                claimed=False,
                task=task,
                reason="runtime_lease_active",
            )

        episode_id = new_runtime_episode_id()
        lease_until = current_time + timedelta(seconds=self.lease_seconds)
        updated = task.model_copy(
            deep=True,
            update={
                "metadata": _metadata_with_runtime(
                    task,
                    {
                        "episode_status": RUNTIME_STATUS_RUNNING,
                        "episode_id": episode_id,
                        "lease_owner": self.lease_owner,
                        "lease_until": lease_until.isoformat(),
                        "started_at": current_time.isoformat(),
                        "completed_at": None,
                        "last_error": None,
                    },
                ),
                "updated_at": current_time,
            },
        )
        TaskRepository(session).update_task_state(updated)
        return RuntimeLeaseClaim(
            task_id=task_id,
            claimed=True,
            task=updated,
            episode_id=episode_id,
        )

    def release_runtime_episode(
        self,
        session: Session,
        *,
        task_id: str,
        episode_id: str | None,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> TaskState:
        """Release a Runtime episode lease if it still belongs to this run."""

        task = _get_locked_task(session, task_id)
        runtime_metadata = _runtime_metadata(task)
        if episode_id is not None and runtime_metadata.get("episode_id") != episode_id:
            return task

        now = utc_now()
        runtime_metadata.update(
            {
                "episode_status": status,
                "lease_until": now.isoformat(),
                "completed_at": now.isoformat(),
                "last_error": error,
            }
        )
        updated = task.model_copy(
            deep=True,
            update={
                "metadata": _metadata_with_runtime(task, runtime_metadata),
                "updated_at": now,
            },
        )
        return TaskRepository(session).update_task_state(updated)

    def record_runtime_exception(
        self,
        *,
        task_id: str,
        episode_id: str | None,
        exc: Exception,
    ) -> None:
        """Persist observable Runtime failure state without marking success."""

        with self.session_factory() as session:
            try:
                task = _get_locked_task(session, task_id)
            except RepositoryNotFoundError:
                return

            error = {
                "error_code": type(exc).__name__,
                "message": str(exc),
            }
            self.release_runtime_episode(
                session,
                task_id=task_id,
                episode_id=episode_id,
                status=RUNTIME_STATUS_ERROR,
                error=error,
            )
            latest = TaskRepository(session).get_task(task_id)
            EventService(session).append_event(
                _build_runtime_event(
                    task=latest,
                    event_type=EventType.TASK_UPDATED,
                    title="Runtime execution error",
                    message=str(exc),
                    severity=EventSeverity.ERROR,
                    payload={
                        "task_id": task_id,
                        "episode_id": episode_id,
                        **error,
                    },
                )
            )
            session.commit()

    def _run_claimed_episode(
        self,
        session: Session,
        *,
        task_id: str,
    ) -> MainAgentEpisodeOutput:
        service = MainAgentService(
            session=session,
            artifact_root=self.artifact_root,
            mcp_mode=self.mcp_mode,
            mock_scenario=self.mock_scenario,
            model=self.model,
            max_turns=self.max_turns,
            runner=self.runner,
            checkpoint=lambda: self._checkpoint_session(session),
        )
        return service.run_episode(task_id)

    @staticmethod
    def _checkpoint_session(session: Session) -> None:
        session.commit()
        session.expire_all()

    def _apply_latest_user_message(
        self,
        session: Session,
        task_id: str,
    ) -> str | None:
        task = TaskRepository(session).get_task(task_id)
        if not _has_open_required_clarification(task):
            return None

        answer_context = latest_user_message_answer_context(
            session=session,
            artifact_root=self.artifact_root,
            task_id=task_id,
        )
        if answer_context is None:
            return None

        now = utc_now()
        answered_questions: list[ClarificationQuestion] = []
        answered_question_ids: list[str] = []
        for question in task.unresolved_questions:
            if (
                question.required
                and enum_value(question.status) == ClarificationStatus.OPEN.value
            ):
                answered_questions.append(
                    question.model_copy(
                        update={
                            "status": ClarificationStatus.ANSWERED.value,
                            "answer": answer_context,
                            "answered_at": now,
                        }
                    )
                )
                answered_question_ids.append(question.question_id)
            else:
                answered_questions.append(question)

        updated = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.RUNNING.value,
                "phase": TaskPhase.PLANNING.value,
                "difficulty": task.difficulty.model_copy(
                    update={"need_clarification": False}
                ),
                "unresolved_questions": answered_questions,
                "updated_at": now,
            },
        )
        TaskRepository(session).update_task_state(updated)
        EventService(session).append_event(
            build_task_event(
                task_id=task_id,
                event_type=EventType.TASK_UPDATED,
                title="Task resumed after user message",
                message="Runtime applied the latest user message to open clarification questions.",
                openai_trace_id=updated.trace.openai_trace_id,
                main_agent_run_id=updated.trace.latest_main_agent_run_id,
                payload={
                    "task_id": task_id,
                    "status": TaskStatus.RUNNING.value,
                    "phase": TaskPhase.PLANNING.value,
                    "answered_question_ids": answered_question_ids,
                },
                created_at=now,
            )
        )
        return answer_context


def run_runtime_start_task(task_id: str, settings: Settings | None = None) -> None:
    """BackgroundTasks entrypoint for starting task Runtime execution."""

    RuntimeService(settings=settings).start_task(task_id)


def run_runtime_resume_task(task_id: str, settings: Settings | None = None) -> None:
    """BackgroundTasks entrypoint for resuming task Runtime execution."""

    RuntimeService(settings=settings).resume_after_user_message(task_id)


def latest_user_message_answer_context(
    *,
    session: Session,
    artifact_root: Path,
    task_id: str,
) -> str | None:
    """Return compact answer context from the latest user message artifact."""

    artifact = _latest_user_message_artifact(session, task_id)
    if artifact is None:
        return None

    stored = ArtifactStore(session=session, artifact_root=artifact_root).read_artifact_content(
        artifact.artifact_id
    )
    try:
        payload = json.loads(stored.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        message = stored.artifact.summary
    else:
        message = str(payload.get("message") or stored.artifact.summary)

    compact = " ".join(message.split())
    if len(compact) > ANSWER_MAX_CHARS:
        compact = f"{compact[: ANSWER_MAX_CHARS - 3]}..."
    return f"{compact}\n\nSource artifact: {artifact.artifact_id}"


def new_runtime_episode_id() -> str:
    return prefixed_id("runtime-episode")


def _get_locked_task(session: Session, task_id: str) -> TaskState:
    row = session.execute(
        select(TaskRow)
        .where(TaskRow.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise RepositoryNotFoundError(f"task not found: {task_id}")
    return TaskState.model_validate(row.state_json)


def _latest_user_message_artifact(session: Session, task_id: str) -> Artifact | None:
    artifacts = ArtifactRepository(session).list_task_artifacts(task_id)
    user_messages = [
        artifact
        for artifact in artifacts
        if _artifact_has_tag(artifact, USER_MESSAGE_TAG)
    ]
    if not user_messages:
        return None
    return max(user_messages, key=lambda artifact: artifact.created_at)


def _artifact_has_tag(artifact: Artifact, tag: str) -> bool:
    return tag in set(artifact.metadata.tags or [])


def _runtime_metadata(task: TaskState) -> dict[str, Any]:
    metadata = task.metadata or {}
    runtime = metadata.get(RUNTIME_METADATA_KEY)
    return dict(runtime) if isinstance(runtime, dict) else {}


def _metadata_with_runtime(
    task: TaskState,
    runtime_metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(task.metadata or {})
    metadata[RUNTIME_METADATA_KEY] = runtime_metadata
    return metadata


def _lease_is_active(runtime_metadata: dict[str, Any], now: datetime) -> bool:
    if runtime_metadata.get("episode_status") != RUNTIME_STATUS_RUNNING:
        return False
    lease_until = _parse_datetime(runtime_metadata.get("lease_until"))
    return lease_until is not None and lease_until > now


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_terminal(task: TaskState) -> bool:
    return enum_value(task.status) in TERMINAL_STATUSES


def _is_runnable(task: TaskState, *, allow_waiting_user: bool) -> bool:
    if _is_terminal(task):
        return False
    if allow_waiting_user:
        return True
    return not _has_open_required_clarification(task) and (
        enum_value(task.status) != TaskStatus.WAITING_USER.value
    )


def _has_open_required_clarification(task: TaskState) -> bool:
    return any(
        question.required
        and enum_value(question.status) == ClarificationStatus.OPEN.value
        for question in task.unresolved_questions
    )


def _runtime_result_status(task: TaskState) -> str:
    status = enum_value(task.status)
    if status == TaskStatus.WAITING_USER.value:
        return "paused"
    if status in TERMINAL_STATUSES:
        return "completed"
    return "idle"


def _build_runtime_event(
    *,
    task: TaskState,
    event_type: EventType,
    title: str,
    message: str,
    severity: EventSeverity,
    payload: dict[str, Any],
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task.task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=severity,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
        ),
        payload=_json_payload(payload),
        created_at=utc_now(),
    )


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in payload.items()
        if value is not None
    }


def _json_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
