from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import episode_output_from_task
from app.agents.output_schema import IntakeClassificationOutput
from app.agents.tools import AgentToolContext
from app.core.config import Settings
from app.core.time import utc_now
from app.models.db_models import Base
from app.models.router_schema import (
    ClarificationQuestion,
    TaskPhase,
    TaskStatus,
)
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeService
from app.services.task_service import TaskService


@pytest.fixture()
def runtime_context(tmp_path: Path) -> Iterator[tuple[Settings, sessionmaker[Session]]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        yield settings, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


class RecordingRunner:
    def __init__(
        self,
        *,
        classification: IntakeClassificationOutput | None = None,
        intake_error: Exception | None = None,
        terminal_status: str | None = None,
    ) -> None:
        self.classification = classification or classification_output()
        self.intake_error = intake_error
        self.terminal_status = terminal_status
        self.calls: list[str] = []

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        self.calls.append("intake")
        if self.intake_error is not None:
            raise self.intake_error
        return self.classification

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        self.calls.append("orchestration")
        repository = TaskRepository(context.session)
        task = repository.get_task(run_config.group_id)
        if self.terminal_status is not None:
            now = utc_now()
            task = repository.update_task_state(
                task.model_copy(
                    deep=True,
                    update={
                        "status": self.terminal_status,
                        "phase": TaskPhase.COMPLETED.value,
                        "completed_at": now,
                        "updated_at": now,
                    },
                )
            )
        return episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Fake runtime runner completed.",
        )


def classification_output(**updates: Any) -> IntakeClassificationOutput:
    values: dict[str, Any] = {
        "normalized_goal": "Create tested motor start/stop PLC logic.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.5,
        "difficulty_confidence": 0.85,
        "difficulty_reasons": ["Requires PLC development and validation."],
        "difficulty_signals": {
            "has_existing_code": False,
            "has_io_points": True,
            "has_timing_logic": False,
            "has_state_machine": False,
            "has_safety_constraints": False,
            "has_emergency_stop": False,
            "has_interlock": False,
            "has_fault_latching": False,
            "has_mode_switching": False,
            "multi_module": False,
            "requirement_incomplete": False,
        },
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }
    values.update(updates)
    return IntakeClassificationOutput.model_validate(values)


def create_task(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    message: str = "Create motor logic.",
) -> str:
    with session_factory() as session:
        result = TaskService(
            session=session,
            artifact_root=settings.artifact_root,
        ).create_task(message=message)
        session.commit()
        return result.task.task_id


def task_state(session_factory: sessionmaker[Session], task_id: str):
    with session_factory() as session:
        return TaskRepository(session).get_task(task_id)


def update_task(
    session_factory: sessionmaker[Session],
    task_id: str,
    **updates: Any,
) -> None:
    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        TaskRepository(session).update_task_state(task.model_copy(deep=True, update=updates))
        session.commit()


def make_waiting_user(
    session_factory: sessionmaker[Session],
    task_id: str,
) -> None:
    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        question = ClarificationQuestion(
            question_id="question-runtime-001",
            question="Which PLC platform should be targeted?",
            reason="The generated code needs a concrete target.",
            required=True,
            status="open",
            asked_at=task.created_at,
        )
        waiting = task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.WAITING_USER.value,
                "phase": TaskPhase.CLARIFYING.value,
                "difficulty": task.difficulty.model_copy(
                    update={"need_clarification": True}
                ),
                "unresolved_questions": [question],
            },
        )
        TaskRepository(session).update_task_state(waiting)
        session.commit()


def append_user_message(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
    message: str = "Use Codesys with StartCmd and StopCmd.",
) -> None:
    with session_factory() as session:
        TaskService(session=session, artifact_root=settings.artifact_root).append_user_message(
            task_id=task_id,
            message=message,
        )
        session.commit()


def runtime_metadata(session_factory: sessionmaker[Session], task_id: str) -> dict[str, Any]:
    metadata = task_state(session_factory, task_id).metadata or {}
    runtime = metadata.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def runtime_service(
    settings: Settings,
    session_factory: sessionmaker[Session],
    runner: RecordingRunner,
    *,
    lease_seconds: int = 300,
) -> RuntimeService:
    return RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        runner=runner,
        lease_seconds=lease_seconds,
    )


def test_start_task_noops_for_terminal_task(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    update_task(session_factory, task_id, status=TaskStatus.SUCCEEDED.value)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "skipped"
    assert result.reason == "terminal_task"
    assert runner.calls == []


def test_start_task_noops_for_waiting_user_task(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    make_waiting_user(session_factory, task_id)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "skipped"
    assert result.reason == "waiting_for_user"
    assert runner.calls == []


def test_duplicate_non_expired_lease_skips_episode(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    update_task(
        session_factory,
        task_id,
        metadata={
            "runtime": {
                "episode_status": "running",
                "episode_id": "runtime-episode-active",
                "lease_until": (utc_now() + timedelta(minutes=5)).isoformat(),
            }
        },
    )
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "skipped"
    assert result.reason == "runtime_lease_active"
    assert runner.calls == []


def test_expired_lease_can_be_reclaimed(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    update_task(
        session_factory,
        task_id,
        metadata={
            "runtime": {
                "episode_status": "running",
                "episode_id": "runtime-episode-expired",
                "lease_until": (utc_now() - timedelta(minutes=5)).isoformat(),
            }
        },
    )
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "idle"
    assert runner.calls == ["intake", "orchestration"]
    metadata = runtime_metadata(session_factory, task_id)
    assert metadata["episode_status"] == "idle"
    assert metadata["episode_id"] != "runtime-episode-expired"


def test_lease_is_released_after_clarification_pause(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    runner = RecordingRunner(
        classification=classification_output(
            need_clarification=True,
            requires_test=False,
            difficulty_signals={
                **classification_output().difficulty_signals.model_dump(mode="json"),
                "requirement_incomplete": True,
            },
            clarification_questions=[
                {
                    "question": "Which PLC platform should be targeted?",
                    "reason": "Platform details are required.",
                    "required": True,
                }
            ],
        )
    )

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "paused"
    assert task_state(session_factory, task_id).status == "waiting_user"
    assert runtime_metadata(session_factory, task_id)["episode_status"] == "idle"


def test_lease_is_released_after_terminal_completion(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    runner = RecordingRunner(terminal_status=TaskStatus.SUCCEEDED.value)

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "completed"
    assert task_state(session_factory, task_id).status == "succeeded"
    assert runtime_metadata(session_factory, task_id)["episode_status"] == "idle"


def test_runtime_exception_records_metadata_and_error_event(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    runner = RecordingRunner(intake_error=RuntimeError("runner exploded"))

    result = runtime_service(settings, session_factory, runner).start_task(task_id)

    assert result.status == "error"
    metadata = runtime_metadata(session_factory, task_id)
    assert metadata["episode_status"] == "error"
    assert metadata["last_error"]["error_code"] == "RuntimeError"
    assert task_state(session_factory, task_id).status != "succeeded"
    with session_factory() as session:
        events = EventService(session).list_visible_events(task_id)
    assert events[-1].title == "Runtime execution error"
    assert events[-1].severity == "error"


def test_waiting_user_resume_answers_questions_and_runs_episode(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    make_waiting_user(session_factory, task_id)
    append_user_message(settings, session_factory, task_id)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).resume_after_user_message(
        task_id
    )

    task = task_state(session_factory, task_id)
    assert result.status == "idle"
    assert runner.calls == ["intake", "orchestration"]
    assert task.status == "running"
    assert task.phase == "planning"
    assert task.unresolved_questions[0].status == "answered"
    assert "Use Codesys" in (task.unresolved_questions[0].answer or "")
    assert "Source artifact:" in (task.unresolved_questions[0].answer or "")


def test_resume_noops_for_terminal_task(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    update_task(session_factory, task_id, status=TaskStatus.CANCELLED.value)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).resume_after_user_message(
        task_id
    )

    assert result.status == "skipped"
    assert result.reason == "terminal_task"
    assert runner.calls == []


def test_resume_noops_for_non_waiting_task(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    append_user_message(settings, session_factory, task_id)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).resume_after_user_message(
        task_id
    )

    assert result.status == "skipped"
    assert result.reason == "task_not_waiting_user"
    assert runner.calls == []


def test_resume_without_user_message_releases_lease_without_running(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)
    make_waiting_user(session_factory, task_id)
    runner = RecordingRunner()

    result = runtime_service(settings, session_factory, runner).resume_after_user_message(
        task_id
    )

    assert result.status == "skipped"
    assert result.reason == "task_not_runnable_after_resume"
    assert runner.calls == []
    assert runtime_metadata(session_factory, task_id)["episode_status"] == "idle"
