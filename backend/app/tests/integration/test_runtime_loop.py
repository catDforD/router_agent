from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import build_main_agent_event, episode_output_from_task
from app.agents.output_schema import IntakeClassificationOutput
from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService
from app.core.config import Settings
from app.core.time import utc_now
from app.mcp.mock_worker import SCENARIO_DEV_TEST_PASS
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import EventType, TaskState
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeRunResult, RuntimeService
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
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    try:
        yield settings, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


class InProcessRuntimeScheduler:
    def __init__(self, runtime: RuntimeService) -> None:
        self.runtime = runtime
        self.jobs: list[tuple[str, str]] = []

    def schedule_start(self, task_id: str) -> None:
        self.jobs.append(("start", task_id))

    def schedule_resume(self, task_id: str) -> None:
        self.jobs.append(("resume", task_id))

    def run_all(self) -> list[RuntimeRunResult]:
        results: list[RuntimeRunResult] = []
        while self.jobs:
            kind, task_id = self.jobs.pop(0)
            if kind == "start":
                results.append(self.runtime.start_task(task_id))
            elif kind == "resume":
                results.append(self.runtime.resume_after_user_message(task_id))
            else:  # pragma: no cover - defensive guard for future test helpers.
                raise AssertionError(f"unknown scheduled job: {kind}")
        return results


class ScriptedToolRunner:
    def __init__(
        self,
        *,
        classifications: list[IntakeClassificationOutput],
        sequence: list[str],
        final_task_status: str | None = None,
        on_intake: Callable[[str], None] | None = None,
    ) -> None:
        self.classifications = list(classifications)
        self.sequence = sequence
        self.final_task_status = final_task_status
        self.on_intake = on_intake
        self.calls: list[str] = []
        self.tool_results: list[AgentToolResult] = []

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
        if self.on_intake is not None:
            self.on_intake(run_config.group_id)
        if not self.classifications:
            raise AssertionError("no scripted classification remains")
        return self.classifications.pop(0)

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
        task_id = run_config.group_id
        tools = AgentToolService(context)
        for action in self.sequence:
            if action == "dev":
                result = tools.call_plc_dev(task_id)
            elif action == "test":
                result = tools.call_plc_test(task_id)
            elif action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue
            elif action == "gate":
                result = tools.run_quality_gate(task_id)
            elif action == "finish":
                result = tools.finish_task(task_id)
            else:
                raise AssertionError(f"unknown fake action: {action}")
            self.tool_results.append(result)
            assert result.status == "applied", result.model_dump(mode="json")

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Fake runtime loop completed deterministic tool sequence.",
        )
        if self.final_task_status is not None:
            output = output.model_copy(
                update={
                    "final_task_status": self.final_task_status,
                    "phase": (
                        "completed"
                        if self.final_task_status
                        in {"succeeded", "partial_failed", "failed", "cancelled"}
                        else task.phase
                    ),
                    "next_recommended_action": "none",
                }
            )
        return output

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Fake runner is running Quality Gate before finish.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id},
                created_at=utc_now(),
            )
        )
        if context.checkpoint is not None:
            context.checkpoint()


def classification(**updates: Any) -> IntakeClassificationOutput:
    values: dict[str, Any] = {
        "normalized_goal": "Create motor control PLC logic with validation.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.55,
        "difficulty_confidence": 0.86,
        "difficulty_reasons": ["Development with validation."],
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


def clarification_classification() -> IntakeClassificationOutput:
    values = classification(
        difficulty_level="L1",
        difficulty_reasons=["Platform details are missing."],
        requires_test=False,
        need_clarification=True,
        clarification_questions=[
            {
                "question": "Which PLC platform and I/O names should be used?",
                "reason": "The worker needs concrete target details.",
                "required": True,
            }
        ],
    ).model_dump(mode="json")
    values["difficulty_signals"]["requirement_incomplete"] = True
    return IntakeClassificationOutput.model_validate(values)


def runtime_service(
    settings: Settings,
    session_factory: sessionmaker[Session],
    runner: ScriptedToolRunner,
) -> RuntimeService:
    return RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
        runner=runner,
    )


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
        ).create_task(
            message=message,
            project_context={"target_plc_language": "ST", "target_platform": "Codesys"},
        )
        session.commit()
        return result.task.task_id


def append_message(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> None:
    with session_factory() as session:
        TaskService(session=session, artifact_root=settings.artifact_root).append_user_message(
            task_id=task_id,
            message="Use Codesys with StartCmd, StopCmd, and MotorRun.",
        )
        session.commit()


def cancel_task(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> None:
    with session_factory() as session:
        TaskService(session=session, artifact_root=settings.artifact_root).cancel_task(task_id)
        session.commit()


def get_task(session_factory: sessionmaker[Session], task_id: str) -> TaskState:
    with session_factory() as session:
        return TaskRepository(session).get_task(task_id)


def event_types(session_factory: sessionmaker[Session], task_id: str) -> list[str]:
    with session_factory() as session:
        return [event.type for event in EventService(session).list_visible_events(task_id)]


def worker_jobs(session_factory: sessionmaker[Session]) -> list[WorkerJobRow]:
    with session_factory() as session:
        return list(session.execute(select(WorkerJobRow)).scalars())


def test_scheduled_start_returns_before_runtime_job_completes_then_succeeds(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    runner = ScriptedToolRunner(
        classifications=[classification()],
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )
    scheduler = InProcessRuntimeScheduler(runtime_service(settings, session_factory, runner))

    task_id = create_task(settings, session_factory)
    scheduler.schedule_start(task_id)

    assert get_task(session_factory, task_id).status == "created"
    assert runner.calls == []

    results = scheduler.run_all()

    assert results[0].status == "completed"
    assert get_task(session_factory, task_id).status == "succeeded"
    assert runner.calls == ["intake", "orchestration"]


def test_checkpointed_progress_is_visible_from_separate_session(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    runner = ScriptedToolRunner(
        classifications=[classification()],
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )
    runtime = runtime_service(settings, session_factory, runner)
    task_id = create_task(settings, session_factory)

    result = runtime.start_task(task_id)
    events = event_types(session_factory, task_id)

    assert result.status == "completed"
    assert "agent.started" in events
    assert "worker.started" in events
    assert "artifact.created" in events
    assert "worker.completed" in events
    assert "gate.passed" in events
    assert "task.succeeded" in events
    assert "agent.completed" in events


def test_cancelled_task_is_not_started_by_scheduled_runtime_job(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    runner = ScriptedToolRunner(
        classifications=[classification()],
        sequence=["dev", "test", "gate", "finish"],
    )
    scheduler = InProcessRuntimeScheduler(runtime_service(settings, session_factory, runner))
    task_id = create_task(settings, session_factory)
    scheduler.schedule_start(task_id)

    cancel_task(settings, session_factory, task_id)
    results = scheduler.run_all()

    assert results[0].status == "skipped"
    assert results[0].reason == "terminal_task"
    assert get_task(session_factory, task_id).status == "cancelled"
    assert worker_jobs(session_factory) == []
    assert runner.calls == []


def test_cancellation_during_runtime_episode_preserves_cancelled_state(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    cancelled_during_intake: list[str] = []

    def cancel_during_intake(task_id: str) -> None:
        cancel_task(settings, session_factory, task_id)
        cancelled_during_intake.append(task_id)

    runner = ScriptedToolRunner(
        classifications=[classification()],
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
        on_intake=cancel_during_intake,
    )
    runtime = runtime_service(settings, session_factory, runner)
    task_id = create_task(settings, session_factory)

    result = runtime.start_task(task_id)
    task = get_task(session_factory, task_id)
    events = event_types(session_factory, task_id)

    assert result.status == "completed"
    assert cancelled_during_intake == [task_id]
    assert task.status == "cancelled"
    assert task.phase == "completed"
    assert worker_jobs(session_factory) == []
    assert "task.cancelled" in events
    assert "worker.started" not in events
    assert events.count("agent.started") == 1


def test_duplicate_runtime_trigger_skips_while_lease_is_active(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    duplicate_results: list[RuntimeRunResult] = []

    def trigger_duplicate(task_id: str) -> None:
        duplicate_runner = ScriptedToolRunner(
            classifications=[classification()],
            sequence=["dev"],
        )
        duplicate_runtime = runtime_service(settings, session_factory, duplicate_runner)
        duplicate_results.append(duplicate_runtime.start_task(task_id))

    runner = ScriptedToolRunner(
        classifications=[classification()],
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
        on_intake=trigger_duplicate,
    )
    runtime = runtime_service(settings, session_factory, runner)
    task_id = create_task(settings, session_factory)

    result = runtime.start_task(task_id)

    assert result.status == "completed"
    assert duplicate_results[0].status == "skipped"
    assert duplicate_results[0].reason == "runtime_lease_active"
    assert runner.calls == ["intake", "orchestration"]


def test_user_message_resume_answers_clarification_and_completes_next_episode(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    runner = ScriptedToolRunner(
        classifications=[clarification_classification()],
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )
    scheduler = InProcessRuntimeScheduler(runtime_service(settings, session_factory, runner))
    task_id = create_task(settings, session_factory)

    scheduler.schedule_start(task_id)
    first_results = scheduler.run_all()

    waiting = get_task(session_factory, task_id)
    assert first_results[0].status == "paused"
    assert waiting.status == "waiting_user"
    assert waiting.unresolved_questions[0].status == "open"

    append_message(settings, session_factory, task_id)
    scheduler.schedule_resume(task_id)
    second_results = scheduler.run_all()

    completed = get_task(session_factory, task_id)
    assert second_results[0].status == "completed"
    assert completed.status == "succeeded"
    assert completed.unresolved_questions[0].status == "answered"
    assert "Use Codesys" in (completed.unresolved_questions[0].answer or "")
    assert runner.calls == ["intake", "orchestration"]
