import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.main_agent import (
    MainAgentService,
    build_main_agent_event,
    episode_output_from_task,
)
from app.agents.output_schema import (
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentPlanStep,
)
from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService
from app.core.time import utc_now
from app.mcp.mock_worker import (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
from app.models.db_models import ArtifactRow, Base, WorkerJobRow
from app.models.router_schema import EventType, TaskState
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.artifact_store import ArtifactStore
from app.services.task_service import TaskService
from app.services.trace_summary import TraceSummaryService


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def task_service(db_session: Session, tmp_path: Path) -> TaskService:
    return TaskService(session=db_session, artifact_root=tmp_path / "artifacts")


class ToolSequenceRunner:
    def __init__(
        self,
        *,
        plan_config: dict[str, Any] | None = None,
        clarification_questions: list[dict[str, Any]] | None = None,
        sequence: list[str],
        final_task_status: str | None = None,
    ) -> None:
        self.plan_config = plan_config or default_plan_config()
        self.clarification_questions = clarification_questions
        self.sequence = sequence
        self.final_task_status = final_task_status
        self.calls: list[str] = []
        self.tool_results: list[AgentToolResult] = []
        self.uses_tool_loop_side_effects = clarification_questions is not None

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
        task = TaskRepository(context.session).get_task(task_id)
        assert worker_jobs(context.session) == []
        assert task.status == "created"
        assert task.phase == "intake"
        assert task.task_type == "unknown"
        if self.clarification_questions is not None:
            result = tools.request_clarification(
                task_id,
                questions=self.clarification_questions,
                rationale_summary="Scripted scenario needs user clarification.",
            )
            assert result.status == "applied", result.model_dump(mode="json")
            task = TaskRepository(context.session).get_task(task_id)
            return self._episode_output(task)

        plan_result = tools.update_plan(
            task_id,
            summary="Scripted scenario prepared task for worker dispatch.",
            plan=[{"order": 1, "action": "run scripted worker sequence"}],
            **self.plan_config,
        )
        assert plan_result.status == "applied", plan_result.model_dump(mode="json")
        for action in self.sequence:
            if action == "dev":
                result = tools.call_plc_dev(task_id)
            elif action == "test":
                result = tools.call_plc_test(task_id)
            elif action == "formal":
                result = tools.call_plc_formal(task_id)
            elif action == "repair":
                result = tools.call_plc_repair(task_id)
            elif action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue
            elif action == "gate":
                result = tools.run_quality_gate(task_id)
            else:
                raise AssertionError(f"unknown fake action: {action}")
            self.tool_results.append(result)
            assert result.status == "applied", result.model_dump(mode="json")

        task = TaskRepository(context.session).get_task(task_id)
        return self._episode_output(task)

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Fake runner is running Quality Gate before final response.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id},
                created_at=utc_now(),
            )
        )

    def _episode_output(self, task: TaskState) -> Any:
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Fake runner completed deterministic tool sequence.",
            decisions=[
                MainAgentDecision(
                    decision_type="tool_sequence",
                    summary="Ran deterministic Main Agent tool sequence.",
                    action="return_final_response",
                    artifact_refs=_output_artifact_refs(self.tool_results),
                    details={
                        "tools": [result.tool for result in self.tool_results],
                        "statuses": [result.status for result in self.tool_results],
                    },
                )
            ],
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
        return output.model_copy(
            update={
                "plan": [
                    MainAgentPlanStep(
                        order=index,
                        action=result.tool,
                        status="completed",
                        tool_name=result.tool,
                    )
                    for index, result in enumerate(self.tool_results, start=1)
                ],
                "metadata": {
                    "tool_count": len(self.tool_results),
                    "tool_names": [result.tool for result in self.tool_results],
                },
            }
        )


class GuardRejectionRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tool_result: AgentToolResult | None = None

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
        plan_result = tools.update_plan(
            task_id,
            summary="Prepare task so test guard reaches missing code validation.",
            plan=[{"order": 1, "action": "call_plc_test"}],
            **default_plan_config(),
        )
        assert plan_result.status == "applied"
        result = tools.call_plc_test(
            task_id,
            rationale_summary="Validate code before delivery.",
        )
        self.tool_result = result
        assert result.status == "rejected"
        task = TaskRepository(context.session).get_task(task_id)
        return episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Fake runner stopped after guard rejection.",
        )


def default_plan_config(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "normalized_goal": "Create motor control PLC logic with validation.",
        "task_type": "new_plc_development",
        "requires_test": True,
        "requires_formal": False,
    }
    values.update(updates)
    return values


def create_task(task_service: TaskService, message: str = "Create motor logic.") -> str:
    return task_service.create_task(
        message=message,
        project_context={"target_plc_language": "ST", "target_platform": "Codesys"},
    ).task.task_id


def run_with_sequence(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
    *,
    runner: ToolSequenceRunner,
    mock_scenario: str,
) -> tuple[str, Any]:
    task_id = create_task(task_service)
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        mock_scenario=mock_scenario,
        runner=runner,
    )
    return task_id, service.run_episode(task_id)


def read_report_content(
    db_session: Session,
    tmp_path: Path,
    artifact_id: str,
) -> dict[str, Any]:
    stored = ArtifactStore(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).read_artifact_content(artifact_id)
    return json.loads(stored.content)


def test_ordinary_l2_development_completes_with_dev_test_gate_final_response(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)

    assert runner.calls == ["orchestration"]
    assert [result.tool for result in runner.tool_results] == [
        "call_plc_dev",
        "call_plc_test",
        "run_quality_gate",
    ]
    assert task.status == "succeeded"
    assert task.gates.latest_test_passed is True
    assert task.gates.latest_formal_passed is None
    assert output.final_task_status == "succeeded"
    assert output.decisions
    assert output.plan
    assert output.artifact_refs
    assert output.gate_summary is not None
    assert output.next_recommended_action == "none"


def test_report_first_success_writes_artifacts_completed_event_then_terminal_success(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    event_types = [event.type for event in events]
    completed_event = next(event for event in events if event.type == "agent.completed")
    artifact_rows = list(
        db_session.execute(select(ArtifactRow).where(ArtifactRow.task_id == task_id))
        .scalars()
        .all()
    )
    artifact_ids = {row.id for row in artifact_rows}

    assert [result.tool for result in runner.tool_results] == [
        "call_plc_dev",
        "call_plc_test",
        "run_quality_gate",
    ]
    assert output.final_task_status == "succeeded"
    assert task.status == "succeeded"
    assert {"final_report", "main_agent_log"} <= {row.type for row in artifact_rows}
    assert completed_event.payload["final_report_artifact_id"] in artifact_ids
    assert completed_event.payload["main_agent_log_artifact_id"] in artifact_ids
    assert event_types.index("agent.completed") < event_types.index(
        "task.succeeded"
    )
    assert task.current_artifacts.final_report is not None
    report = read_report_content(
        db_session,
        tmp_path,
        task.current_artifacts.final_report.artifact_id,
    )
    assert report["report_version"] == 1
    assert report["final_task_status"] == "succeeded"
    assert report["delivery_artifacts"]["final_plc_code"]["artifact_id"] == (
        task.current_artifacts.current_code.artifact_id
    )
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        task.current_artifacts.latest_test_report.artifact_id
    )
    assert report["delivery_artifacts"]["gate_report"]["artifact_id"] == (
        task.current_artifacts.latest_gate_report.artifact_id
    )
    assert report["unresolved_items"]["blocking_failure_count"] == 0


def test_report_first_partial_failed_report_records_unresolved_failures(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="partial_failed",
    )

    task_id, output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    event_types = [event.type for event in events]

    assert output.final_task_status == "partial_failed"
    assert task.status == "partial_failed"
    assert task.gates.has_blocking_failure is True
    assert any(failure.status == "open" for failure in task.failures)
    assert task.current_artifacts.final_report is not None
    assert event_types.index("agent.completed") < event_types.index(
        "task.partial_failed"
    )
    report = read_report_content(
        db_session,
        tmp_path,
        task.current_artifacts.final_report.artifact_id,
    )
    assert report["final_task_status"] == "partial_failed"
    assert report["delivery_artifacts"]["final_plc_code"]["artifact_id"] == (
        task.current_artifacts.current_code.artifact_id
    )
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        task.current_artifacts.latest_test_report.artifact_id
    )
    assert report["delivery_artifacts"]["gate_report"]["artifact_id"] == (
        task.current_artifacts.latest_gate_report.artifact_id
    )
    assert report["unresolved_items"]["blocking_failure_count"] >= 1
    assert report["unresolved_items"]["open_failures"]


def test_guard_rejection_is_visible_through_main_agent_tool_result(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = GuardRejectionRunner()

    task_id, output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    tool_result = next(
        event
        for event in events
        if event.type == "agent.tool_result"
        and event.payload["status"] == "rejected"
    )

    assert runner.calls == ["orchestration"]
    assert runner.tool_result is not None
    assert runner.tool_result.status == "rejected"
    assert task.status == "running"
    assert output.final_task_status == "running"
    assert tool_result.payload["status"] == "rejected"
    assert tool_result.payload["details"]["violation"]["code"] == "missing_current_code"
    assert worker_jobs(db_session) == []


def test_safety_critical_l3_development_runs_test_and_formal_before_final_response(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        plan_config=default_plan_config(
            requires_formal=True,
        ),
        sequence=["dev", "test", "formal", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, _output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)

    assert task.status == "succeeded"
    assert task.gates.test_required is True
    assert task.gates.formal_required is True
    assert task.gates.latest_test_passed is True
    assert task.gates.latest_formal_passed is True


def test_test_failure_triggers_repair_and_regression_before_final_response(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "repair", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, _output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)

    assert task.status == "succeeded"
    assert task.runtime_limits.repair_rounds == 1
    assert task.gates.latest_test_passed is True
    assert task.gates.regression_required is False
    assert task.gates.has_blocking_failure is False
    assert [failure.status for failure in task.failures] == ["resolved"]


def test_formal_failure_triggers_repair_test_and_formal_regression(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        plan_config=default_plan_config(
            requires_formal=True,
        ),
        sequence=[
            "dev",
            "test",
            "formal",
            "repair",
            "test",
            "formal",
            "finalizing",
            "gate",
        ],
        final_task_status="succeeded",
    )

    task_id, _output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)

    assert task.status == "succeeded"
    assert task.runtime_limits.repair_rounds == 1
    assert task.gates.latest_test_passed is True
    assert task.gates.latest_formal_passed is True
    assert task.gates.regression_required is False
    assert task.gates.formal_regression_required is False
    assert task.gates.has_blocking_failure is False
    assert [failure.status for failure in task.failures] == ["resolved"]


def test_clarification_required_task_creates_no_worker_jobs(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        clarification_questions=[
            {
                "question": "Which PLC platform and I/O names should be used?",
                "reason": "The worker needs concrete target details.",
                "required": True,
            }
        ],
        sequence=[],
    )

    task_id, output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)

    assert runner.calls == ["orchestration"]
    assert task.status == "waiting_user"
    assert task.phase == "clarifying"
    assert worker_jobs(db_session) == []
    assert non_raw_artifacts(db_session) == []
    assert output.final_task_status == "waiting_user"
    assert output.open_clarification_question_ids == [
        task.unresolved_questions[0].question_id
    ]
    assert output.next_recommended_action == "ask_user"


def test_worker_inputs_inherit_main_agent_trace_context(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, _output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    task = TaskRepository(db_session).get_task(task_id)
    dev_job = db_session.execute(
        select(WorkerJobRow).where(WorkerJobRow.worker_type == "plc-dev")
    ).scalar_one()
    trace_context = dev_job.input_json["trace_context"]

    assert trace_context["openai_trace_id"] == task.trace.openai_trace_id
    assert trace_context["main_agent_run_id"] == task.trace.latest_main_agent_run_id
    assert trace_context["worker_job_id"] == dev_job.id

    summary = TraceSummaryService(db_session).get_task_trace_summary(task_id)
    dev_summary = next(
        job for job in summary.worker_jobs if job.worker_job_id == dev_job.id
    )
    assert summary.openai_trace_id == task.trace.openai_trace_id
    assert summary.latest_main_agent_run_id == task.trace.latest_main_agent_run_id
    assert summary.main_agent_runs[0].started_event_id is not None
    assert summary.main_agent_runs[0].final_report_artifact_id is not None
    assert dev_summary.openai_trace_id == task.trace.openai_trace_id
    assert dev_summary.main_agent_run_id == task.trace.latest_main_agent_run_id
    assert dev_summary.produced_artifact_ids
    assert any(
        event.correlation.openai_trace_id == task.trace.openai_trace_id
        and event.correlation.main_agent_run_id == task.trace.latest_main_agent_run_id
        for event in summary.events
        if event.type == "worker.started"
    )


def test_main_agent_events_are_visible_in_orchestration_timeline(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = ToolSequenceRunner(
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )

    task_id, _output = run_with_sequence(
        db_session,
        tmp_path,
        task_service,
        runner=runner,
        mock_scenario=SCENARIO_DEV_TEST_PASS,
    )
    events = [event.type for event in EventService(db_session).list_visible_events(task_id)]

    assert "agent.started" in events
    assert "agent.plan_updated" in events
    assert "agent.finalizing" in events
    assert "agent.completed" in events
    assert "gate.passed" in events
    assert "task.succeeded" in events


def _output_artifact_refs(
    results: list[AgentToolResult],
) -> list[MainAgentArtifactReference]:
    refs: list[MainAgentArtifactReference] = []
    seen: set[str] = set()
    for result in results:
        for artifact_ref in result.artifact_refs:
            if artifact_ref.artifact_id in seen:
                continue
            seen.add(artifact_ref.artifact_id)
            refs.append(
                MainAgentArtifactReference.model_validate(
                    artifact_ref.model_dump(mode="json")
                )
            )
    return refs


def worker_jobs(db_session: Session) -> list[WorkerJobRow]:
    return list(db_session.execute(select(WorkerJobRow)).scalars())


def non_raw_artifacts(db_session: Session) -> list[ArtifactRow]:
    return list(
        db_session.execute(
            select(ArtifactRow).where(ArtifactRow.type != "raw_user_request")
        ).scalars()
    )
