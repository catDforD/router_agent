from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.main_agent import MainAgentService, MaxTurnsExceeded
from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.tools import AgentToolContext, AgentToolService
from app.core.config import Settings
from app.mcp.adapter import McpAdapter
from app.mcp.client import PlcMcpConnectionError
from app.mcp.draft import LlmWorkerDraftOutput, McpWorkerRequest
from app.mcp.mock_worker import SCENARIO_DEV_TEST_PASS
from app.mcp.normalizer import (
    ERROR_MCP_CONNECTION_ERROR,
    ERROR_MCP_TIMEOUT,
    ERROR_WORKER_EXECUTION_ERROR,
    ERROR_WORKER_SCHEMA_INVALID,
)
from app.models.db_models import Base, WorkerJobRow
from app.models.router_schema import (
    TaskPhase,
    TaskState,
    TaskStatus,
    TraceContext,
    WorkerJobRef,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.artifact_store import ArtifactStore
from app.services.event_service import EventService
from app.services.runtime_service import RuntimeService
from app.services.task_service import TaskService
from app.workers.worker_input_builder import build_worker_input
from app.workers.worker_result_handler import WorkerResultHandler


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


def test_cancelled_task_is_not_started_by_runtime(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    runner = NeverRunner()
    task_id = create_task(settings, session_factory)

    cancel_task(settings, session_factory, task_id)
    result = RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        runner=runner,
    ).start_task(task_id)

    assert result.status == "skipped"
    assert result.reason == "terminal_task"
    assert runner.calls == []
    assert worker_jobs(session_factory) == []
    assert visible_event_types(session_factory, task_id) == [
        "task.created",
        "task.cancelled",
    ]


def test_cancel_clears_task_active_worker_state_but_preserves_worker_job(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    with session_factory() as session:
        task = make_running_task(session, task_id)
        payload = build_worker_input(
            task,
            WorkerType.PLC_DEV,
            worker_job_id="worker-job-active-001",
            trace_context=TraceContext(worker_job_id="worker-job-active-001"),
        )
        WorkerJobRepository(session).create_job(payload)
        active = WorkerJobRef(
            worker_job_id=payload.worker_job_id,
            worker_type=payload.worker_type,
            status="running",
            objective=payload.objective,
            started_at=payload.created_at,
        )
        updated = task.model_copy(
            deep=True,
            update={
                "active_worker_jobs": [active],
                "runtime_limits": task.runtime_limits.model_copy(
                    update={"active_parallel_workers": 1}
                ),
            },
        )
        TaskRepository(session).update_task_state(updated)
        session.commit()

    cancel_task(settings, session_factory, task_id)
    cancelled = get_task(session_factory, task_id)
    jobs = worker_jobs(session_factory)

    assert cancelled.status == "cancelled"
    assert cancelled.active_worker_jobs == []
    assert cancelled.runtime_limits.active_parallel_workers == 0
    assert len(jobs) == 1
    assert jobs[0].id == "worker-job-active-001"
    assert jobs[0].status == "running"


def test_late_worker_result_after_cancel_is_audit_only(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    with session_factory() as session:
        task = make_running_task(session, task_id)
        payload = build_worker_input(
            task,
            WorkerType.PLC_DEV,
            worker_job_id="worker-job-late-001",
            trace_context=TraceContext(worker_job_id="worker-job-late-001"),
        )
        session.commit()

    cancel_task(settings, session_factory, task_id)
    after_cancel = get_task(session_factory, task_id)

    with session_factory() as session:
        result = McpAdapter(
            session=session,
            artifact_root=settings.artifact_root,
        ).call_worker(payload)
        handled = WorkerResultHandler(session).handle_worker_result(result)
        session.commit()

    final_task = get_task(session_factory, task_id)
    events = visible_event_types(session_factory, task_id)
    jobs = worker_jobs(session_factory)

    assert handled.applied is False
    assert final_task.status == "cancelled"
    assert final_task.phase == "completed"
    assert final_task.current_artifacts == after_cancel.current_artifacts
    assert final_task.gates == after_cancel.gates
    assert final_task.failures == after_cancel.failures
    assert final_task.assumptions == after_cancel.assumptions
    assert final_task.unresolved_questions == after_cancel.unresolved_questions
    assert final_task.completed_worker_job_ids == after_cancel.completed_worker_job_ids
    assert jobs[-1].id == "worker-job-late-001"
    assert jobs[-1].status == "completed"
    assert "worker.completed" in events


def test_worker_error_normalization_is_diagnosable(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context

    assert_normalized_worker_error(
        settings,
        session_factory,
        adapter_kwargs={},
        scenario="worker_timeout",
        expected_status="timeout",
        expected_error_code=ERROR_MCP_TIMEOUT,
        expected_event_type="worker.timeout",
    )
    assert_normalized_worker_error(
        settings,
        session_factory,
        adapter_kwargs={"mock_runner": invalid_worker_output},
        expected_status="error",
        expected_error_code=ERROR_WORKER_SCHEMA_INVALID,
        expected_event_type="worker.error",
    )
    assert_normalized_worker_error(
        settings,
        session_factory,
        adapter_kwargs={"mock_runner": exploding_worker},
        expected_status="error",
        expected_error_code=ERROR_WORKER_EXECUTION_ERROR,
        expected_event_type="worker.error",
    )
    assert_normalized_worker_error(
        settings,
        session_factory,
        adapter_kwargs={
            "mcp_mode": "real",
            "mcp_client": FakeRealMcpClient(
                PlcMcpConnectionError(
                    "connection failed",
                    details={"exception_type": "ConnectError"},
                )
            ),
        },
        expected_status="error",
        expected_error_code=ERROR_MCP_CONNECTION_ERROR,
        expected_event_type="worker.error",
    )


def test_worker_call_budget_exhaustion_is_guard_rejected_before_dispatch(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    with session_factory() as session:
        task = make_running_task(session, task_id)
        exhausted = task.model_copy(
            deep=True,
            update={
                "runtime_limits": task.runtime_limits.model_copy(
                    update={
                        "worker_calls_used": task.runtime_limits.max_worker_calls,
                    }
                )
            },
        )
        TaskRepository(session).update_task_state(exhausted)
        recorder = MainAgentObservabilityRecorder(
            session=session,
            artifact_root=settings.artifact_root,
            task_id=task_id,
            main_agent_run_id="main-agent-run-budget",
        )
        result = AgentToolService(
            AgentToolContext(
                session=session,
                artifact_root=settings.artifact_root,
                observability_recorder=recorder,
            )
        ).call_plc_dev(task_id)
        events = EventService(session).list_visible_events(task_id)
        rows = list(session.execute(select(WorkerJobRow)).scalars())

    assert result.status == "rejected"
    assert result.violation is not None
    assert result.violation.code == "worker_call_limit_exceeded"
    assert rows == []
    assert [event.type for event in events] == [
        "task.created",
        "agent.turn_started",
        "agent.tool_called",
        "agent.tool_result",
    ]
    assert events[-1].payload["details"]["violation"]["code"] == (
        "worker_call_limit_exceeded"
    )


def test_main_agent_max_turns_marks_non_terminal_task_failed(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    with session_factory() as session:
        output = MainAgentService(
            session=session,
            artifact_root=settings.artifact_root,
            runner=MaxTurnsRunner(),
        ).run_episode(task_id)
        session.commit()

    task = get_task(session_factory, task_id)
    events = visible_events(session_factory, task_id)
    event_types = [event.type for event in events]

    assert output.error_code == "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    assert task.status == "failed"
    assert task.phase == "completed"
    assert task.completed_at is not None
    assert task.current_artifacts.final_report is not None
    assert event_types[-3:] == [
        "agent.decision",
        "agent.completed",
        "task.failed",
    ]
    assert events[-3].severity == "error"
    assert events[-3].payload["error_code"] == "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    assert events[-2].payload["final_report_artifact_id"] == (
        task.current_artifacts.final_report.artifact_id
    )
    assert events[-1].type == "task.failed"
    report = read_artifact_json(
        settings,
        session_factory,
        task.current_artifacts.final_report.artifact_id,
    )
    assert report["report_version"] == 1
    assert report["final_task_status"] == "failed"
    assert report["main_agent_output_summary"]["error_code"] == (
        "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    )


def test_runtime_releases_lease_after_main_agent_max_turn_failure(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    result = RuntimeService(
        settings=settings,
        session_factory=session_factory,
        artifact_root=settings.artifact_root,
        runner=MaxTurnsRunner(),
    ).start_task(task_id)
    task = get_task(session_factory, task_id)

    assert result.status == "completed"
    assert task.status == "failed"
    assert task.metadata is not None
    assert task.metadata["runtime"]["episode_status"] == "idle"
    assert task.metadata["runtime"]["completed_at"] is not None


def test_stale_max_turn_error_does_not_overwrite_cancelled_task(
    runtime_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = runtime_context
    task_id = create_task(settings, session_factory)

    with session_factory() as session:
        output = MainAgentService(
            session=session,
            artifact_root=settings.artifact_root,
            runner=CancelThenMaxTurnsRunner(settings, session_factory),
            checkpoint=session.commit,
        ).run_episode(task_id)
        session.commit()

    task = get_task(session_factory, task_id)
    event_types = visible_event_types(session_factory, task_id)

    assert output.error_code == "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    assert task.status == "cancelled"
    assert "task.cancelled" in event_types
    assert "task.failed" not in event_types


class NeverRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_intake(self, **kwargs: Any) -> Any:
        self.calls.append("intake")
        raise AssertionError("cancelled task should not run intake")

    def run_orchestration(self, **kwargs: Any) -> Any:
        self.calls.append("orchestration")
        raise AssertionError("cancelled task should not run orchestration")


class MaxTurnsRunner:
    def run_intake(self, **kwargs: Any) -> Any:
        raise MaxTurnsExceeded("too many turns")

    def run_orchestration(self, **kwargs: Any) -> Any:
        raise AssertionError("orchestration should not run after intake max turns")


class CancelThenMaxTurnsRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory

    def run_intake(self, **kwargs: Any) -> Any:
        cancel_task(self.settings, self.session_factory, kwargs["run_config"].group_id)
        raise MaxTurnsExceeded("too many turns after cancellation")

    def run_orchestration(self, **kwargs: Any) -> Any:
        raise AssertionError("orchestration should not run after intake max turns")


class FakeRealMcpClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[tuple[str, McpWorkerRequest]] = []

    def call_worker_tool(
        self,
        tool_name: str,
        request: McpWorkerRequest,
    ) -> LlmWorkerDraftOutput:
        self.calls.append((tool_name, request))
        raise self.error


def create_task(
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> str:
    with session_factory() as session:
        result = TaskService(
            session=session,
            artifact_root=settings.artifact_root,
        ).create_task(
            message="Create motor logic.",
            project_context={"target_plc_language": "ST", "target_platform": "Codesys"},
        )
        session.commit()
        return result.task.task_id


def cancel_task(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_id: str,
) -> None:
    with session_factory() as session:
        TaskService(session=session, artifact_root=settings.artifact_root).cancel_task(
            task_id
        )
        session.commit()


def make_running_task(session: Session, task_id: str) -> TaskState:
    task = TaskRepository(session).get_task(task_id)
    running = task.model_copy(
        deep=True,
        update={
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "task_type": "new_plc_development",
            "normalized_goal": task.raw_user_request,
        },
    )
    return TaskRepository(session).update_task_state(running)


def get_task(
    session_factory: sessionmaker[Session],
    task_id: str,
) -> TaskState:
    with session_factory() as session:
        return TaskRepository(session).get_task(task_id)


def visible_events(
    session_factory: sessionmaker[Session],
    task_id: str,
):
    with session_factory() as session:
        return EventService(session).list_visible_events(task_id)


def visible_event_types(
    session_factory: sessionmaker[Session],
    task_id: str,
) -> list[str]:
    return [str(event.type) for event in visible_events(session_factory, task_id)]


def worker_jobs(session_factory: sessionmaker[Session]) -> list[WorkerJobRow]:
    with session_factory() as session:
        return list(session.execute(select(WorkerJobRow)).scalars())


def worker_job(session_factory: sessionmaker[Session], worker_job_id: str):
    with session_factory() as session:
        return WorkerJobRepository(session).get_job(worker_job_id)


def read_artifact_json(
    settings: Settings,
    session_factory: sessionmaker[Session],
    artifact_id: str,
) -> dict[str, Any]:
    with session_factory() as session:
        stored = ArtifactStore(
            session=session,
            artifact_root=settings.artifact_root,
        ).read_artifact_content(artifact_id)
        return json.loads(stored.content)


def assert_normalized_worker_error(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    adapter_kwargs: dict[str, Any],
    expected_status: str,
    expected_error_code: str,
    expected_event_type: str,
    scenario: str | None = None,
) -> None:
    task_id = create_task(settings, session_factory)
    with session_factory() as session:
        task = make_running_task(session, task_id)
        payload = build_worker_input(task, WorkerType.PLC_DEV)
        result = McpAdapter(
            session=session,
            artifact_root=settings.artifact_root,
            **adapter_kwargs,
        ).call_worker(payload, scenario=scenario)
        session.commit()

    events = visible_events(session_factory, task_id)
    job = worker_job(session_factory, payload.worker_job_id)

    assert result.execution_status == expected_status
    assert result.error is not None
    assert result.error.error_code == expected_error_code
    assert job.status == expected_status
    assert job.id == payload.worker_job_id
    terminal_event = events[-1]
    assert terminal_event.type == expected_event_type
    assert terminal_event.correlation.worker_job_id == payload.worker_job_id
    assert terminal_event.payload["worker_job_id"] == payload.worker_job_id
    assert terminal_event.payload["error_code"] == expected_error_code


def invalid_worker_output(worker_input: Any, *, scenario: str) -> object:
    return {"invalid": True}


def exploding_worker(worker_input: Any, *, scenario: str) -> object:
    raise RuntimeError("worker exploded")
