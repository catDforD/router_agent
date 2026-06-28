import json
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.tools import AgentToolContext, AgentToolService
from app.agents.main_agent import MainAgentService
from app.agents.output_schema import MainAgentEpisodeOutput, MainAgentGateSummary
from app.core.time import utc_now
from app.mcp.adapter import McpAdapter
from app.mcp.draft import McpInputFileSnapshot, validate_worker_draft_output
from app.mcp.subagent_client import draft_from_subagent_events, parse_sse_events
from app.models.db_models import ArtifactRow, Base
from app.models.router_schema import (
    ArtifactType,
    Failure,
    FailureSource,
    FailureStatus,
    GateState,
    NextRecommendedAction,
    Severity,
    TaskPhase,
    TaskState,
    TaskStatus,
    WorkerExecutionStatus,
    WorkerMetrics,
    WorkerOutcome,
    WorkerOutcomeStatus,
    WorkerResult,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.event_service import EventService
from app.services.quality_gate import QualityGateService
from app.services.task_service import TaskService
from app.services.trace_summary import TraceSummaryService
from app.workers.worker_input_builder import build_worker_input
from app.workers.worker_result_handler import handle_worker_result


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
    return TaskService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        session_workspace_root=tmp_path / "workspaces",
    )


def test_create_task_writes_raw_request_file(
    db_session: Session,
    task_service: TaskService,
) -> None:
    result = task_service.create_task(message="Create pump logic.")
    task = TaskRepository(db_session).get_task(result.task.task_id)

    assert result.raw_user_request_path == task.current_files.raw_user_request
    assert result.raw_user_request_path in task.current_files.all_paths

    request_path = Path(task.workspace.root) / result.raw_user_request_path
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["message"] == "Create pump logic."


def test_worker_input_builder_selects_file_paths(
    db_session: Session,
    task_service: TaskService,
) -> None:
    created = task_service.create_task(message="Test motor code.").task
    task = _with_files(
        created,
        current_code="src/main.st",
        requirements=".router/requests/requirements.json",
    )
    TaskRepository(db_session).update_task_state(task)

    worker_input = build_worker_input(
        task,
        WorkerType.PLC_TEST,
        worker_job_id="worker-job-test-001",
    )

    assert worker_input.schema_version == "router.v2"
    assert worker_input.input_paths == [
        ".router/requests/requirements.json",
        "src/main.st",
    ]
    assert worker_input.output_paths == [
        ".router/reports/worker-job-test-001/test_report.json"
    ]
    assert [item.path for item in worker_input.expected_outputs] == worker_input.output_paths


def test_worker_input_builder_uses_unique_dispatch_key_and_stable_signature(
    db_session: Session,
    task_service: TaskService,
) -> None:
    created = task_service.create_task(message="Test motor code.").task
    task = _with_files(
        created,
        current_code="src/plc_code.st",
        requirements=".router/requests/requirements.json",
    )
    TaskRepository(db_session).update_task_state(task)

    first = build_worker_input(task, WorkerType.PLC_TEST)
    second = build_worker_input(task, WorkerType.PLC_TEST)

    assert first.worker_job_id != second.worker_job_id
    assert first.idempotency_key != second.idempotency_key
    assert first.idempotency_key == (
        f"{task.task_id}:plc-test:{first.worker_job_id}"
    )
    assert first.metadata is not None
    assert second.metadata is not None
    assert first.metadata["input_signature"] == second.metadata["input_signature"]


def test_repeated_explicit_plc_test_creates_two_jobs_then_debounces(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    created = task_service.create_task(message="Test motor code.").task
    task = _with_files(
        created,
        current_code="src/plc_code.st",
        requirements=".router/requests/requirements.json",
    )
    TaskRepository(db_session).update_task_state(task)
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            mock_scenario="test_failed_then_repair_pass",
        )
    )

    first = service.plc_test(task.task_id)
    second = service.plc_test(task.task_id)
    third = service.plc_test(task.task_id)
    jobs = WorkerJobRepository(db_session).list_task_jobs(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)

    assert first.status == "applied"
    assert second.status == "applied"
    assert third.status == "rejected"
    assert third.violation is not None
    assert third.violation.code == "worker_retry_debounce"
    assert len(jobs) == 2
    assert jobs[0].id != jobs[1].id
    assert jobs[0].idempotency_key != jobs[1].idempotency_key
    assert jobs[0].input.metadata is not None
    assert jobs[1].input.metadata is not None
    assert (
        jobs[0].input.metadata["input_signature"]
        == jobs[1].input.metadata["input_signature"]
    )
    assert updated.runtime_limits.worker_calls_used == 2


def test_plc_test_accepts_manually_written_code_without_requirements(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    created = task_service.create_task(message="Write and test start stop logic.").task
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=Path(created.workspace.root),
            execution_mode="local_full_access",
        )
    )

    write_result = service.write_file(
        created.task_id,
        path="motor_start_stop.st",
        content="FUNCTION_BLOCK FB_MotorStartStop\nEND_FUNCTION_BLOCK\n",
    )
    test_result = service.plc_test(created.task_id)
    updated = TaskRepository(db_session).get_task(created.task_id)
    jobs = WorkerJobRepository(db_session).list_task_jobs(created.task_id)

    assert write_result.status == "applied"
    assert updated.current_files.current_code == "motor_start_stop.st"
    assert updated.current_files.requirements is None
    assert test_result.status == "applied"
    assert test_result.worker_type == "plc-test"
    assert jobs[0].input.input_paths == ["motor_start_stop.st"]


def test_worker_result_projects_written_and_report_paths(
    db_session: Session,
    task_service: TaskService,
) -> None:
    task = task_service.create_task(message="Generate motor code.").task
    worker_input = build_worker_input(
        task,
        WorkerType.PLC_DEV,
        worker_job_id="worker-job-dev-001",
    )
    WorkerJobRepository(db_session).create_job(worker_input)

    now = utc_now()
    result = WorkerResult(
        schema_version="router.v2",
        task_id=task.task_id,
        worker_job_id=worker_input.worker_job_id,
        worker_type=WorkerType.PLC_DEV,
        mcp_tool=worker_input.mcp_tool,
        execution_status=WorkerExecutionStatus.COMPLETED,
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
        ),
        summary="Generated code.",
        read_paths=list(worker_input.input_paths),
        written_paths=[
            "src/plc_code.st",
            ".router/reports/worker-job-dev-001/requirements.json",
        ],
        report_paths=[".router/reports/worker-job-dev-001/requirements.json"],
        diagnostics=[],
        assumptions=[],
        failures=[],
        metrics=WorkerMetrics(),
        next_recommended_action=NextRecommendedAction.TEST,
        trace_context=worker_input.trace_context,
        started_at=worker_input.created_at,
        completed_at=now,
    )

    handled = handle_worker_result(result, session=db_session)

    assert handled.applied is True
    assert handled.task.current_files.current_code == "src/plc_code.st"
    assert (
        ".router/reports/worker-job-dev-001/requirements.json"
        in handled.task.current_files.all_paths
    )
    assert db_session.scalar(select(func.count()).select_from(ArtifactRow)) == 0


def test_repair_success_without_code_change_only_requires_summary(
    db_session: Session,
    task_service: TaskService,
) -> None:
    created = task_service.create_task(message="Repair motor code.").task
    task = _with_open_test_failure(created)
    TaskRepository(db_session).update_task_state(task)
    worker_input = build_worker_input(
        task,
        WorkerType.PLC_REPAIR,
        worker_job_id="worker-job-repair-no-code-draft",
    )
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "compilation_report_json",
                    "content": {
                        "summary": "Validation passed; no source change needed.",
                        "compilation_success": True,
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(
        worker_input,
        [
            McpInputFileSnapshot(
                path="src/plc_code.st",
                type=ArtifactType.PLC_CODE,
                version=1,
                content="FUNCTION_BLOCK FB_MotorControl\nEND_FUNCTION_BLOCK\n",
            )
        ],
        events,
    )

    validate_worker_draft_output(draft, worker_input)
    assert draft.outcome.status == "passed"
    assert [write.artifact_type for write in draft.artifact_writes] == [
        "repair_summary"
    ]


def test_dev_subagent_infers_io_contract_from_plain_var_block(
    db_session: Session,
    task_service: TaskService,
) -> None:
    task = task_service.create_task(
        message="Generate start stop motor logic."
    ).task
    worker_input = build_worker_input(
        task,
        WorkerType.PLC_DEV,
        worker_job_id="worker-job-dev-io-contract",
    )
    code = """FUNCTION_BLOCK FB_MotorControl
VAR
    // input variables
    START_PB, STOP_PB : BOOL;
    // output variables
    MOTOR_RUN : BOOL;
    RUN_IND : BOOL := FALSE;
END_VAR
END_FUNCTION_BLOCK
"""
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "st_code_json",
                    "content": {
                        "code": code,
                        "file_name": "plc_code.st",
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(worker_input, [], events)

    validate_worker_draft_output(draft, worker_input)
    io_contract = next(
        write
        for write in draft.artifact_writes
        if write.artifact_type == ArtifactType.IO_CONTRACT.value
    )
    assert io_contract.content == {
        "inputs": [
            {"name": "START_PB", "type": "BOOL"},
            {"name": "STOP_PB", "type": "BOOL"},
        ],
        "outputs": [
            {"name": "MOTOR_RUN", "type": "BOOL"},
            {"name": "RUN_IND", "type": "BOOL"},
        ],
    }


def test_passed_repair_without_code_change_closes_selected_failure(
    db_session: Session,
    task_service: TaskService,
) -> None:
    created = task_service.create_task(message="Repair motor code.").task
    task = _with_open_test_failure(created)
    TaskRepository(db_session).update_task_state(task)
    worker_input = build_worker_input(
        task,
        WorkerType.PLC_REPAIR,
        worker_job_id="worker-job-repair-no-code-result",
    )
    WorkerJobRepository(db_session).create_job(worker_input)
    now = utc_now()
    result = WorkerResult(
        schema_version="router.v2",
        task_id=task.task_id,
        worker_job_id=worker_input.worker_job_id,
        worker_type=WorkerType.PLC_REPAIR,
        mcp_tool=worker_input.mcp_tool,
        execution_status=WorkerExecutionStatus.COMPLETED,
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
        ),
        summary="Repair validation passed without code changes.",
        read_paths=list(worker_input.input_paths),
        written_paths=[
            ".router/reports/worker-job-repair-no-code-result/repair_summary.json"
        ],
        report_paths=[
            ".router/reports/worker-job-repair-no-code-result/repair_summary.json"
        ],
        diagnostics=[],
        assumptions=[],
        failures=[],
        metrics=WorkerMetrics(),
        next_recommended_action=NextRecommendedAction.NONE,
        trace_context=worker_input.trace_context,
        started_at=worker_input.created_at,
        completed_at=now,
    )

    handled = handle_worker_result(result, session=db_session)
    failure = handled.task.failures[0]

    assert failure.status == "resolved"
    assert failure.resolved_by_worker_job_id == worker_input.worker_job_id
    assert failure.resolved_by_path == (
        ".router/reports/worker-job-repair-no-code-result/repair_summary.json"
    )
    assert handled.task.current_files.current_code == "src/plc_code.st"
    assert handled.task.current_files.latest_repair_summary == (
        ".router/reports/worker-job-repair-no-code-result/repair_summary.json"
    )
    assert handled.task.gates.has_blocking_failure is False
    assert handled.task.gates.regression_required is False
    assert handled.task.gates.formal_regression_required is False
    assert handled.task.phase == "quality_gate"


def test_mcp_adapter_reads_workspace_files_and_writes_reports(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    created = task_service.create_task(message="Test motor code.").task
    task = _with_files(created, current_code="src/main.st")
    workspace_root = Path(task.workspace.root)
    code_path = workspace_root / "src/main.st"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text(
        "FUNCTION_BLOCK FB_MotorControl\nEND_FUNCTION_BLOCK\n",
        encoding="utf-8",
    )
    TaskRepository(db_session).update_task_state(task)
    worker_input = build_worker_input(
        task,
        WorkerType.PLC_TEST,
        worker_job_id="worker-job-test-adapter-001",
    )

    result = McpAdapter(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        mcp_mode="mock",
    ).call_worker(worker_input)

    assert result.read_paths == ["src/main.st"]
    assert result.report_paths == [
        ".router/reports/worker-job-test-adapter-001/test_report.json"
    ]
    assert (workspace_root / result.report_paths[0]).is_file()
    assert db_session.scalar(select(func.count()).select_from(ArtifactRow)) == 0


def test_quality_gate_writes_workspace_report_without_artifact_store(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task = task_service.create_task(message="Explain this PLC project.").task

    result = QualityGateService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).run_quality_gate(task.task_id)

    updated = TaskRepository(db_session).get_task(task.task_id)
    report_path = Path(updated.workspace.root) / result.gate_report_path
    artifact_count = db_session.scalar(select(func.count()).select_from(ArtifactRow))

    assert result.gate_report_path == updated.current_files.latest_gate_report
    assert result.gate_report_path in updated.current_files.all_paths
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trace = TraceSummaryService(db_session).get_task_trace_summary(task.task_id)
    assert report["schema_version"] == "router.v2"
    assert "evidence_artifact_ids" not in report["assessment"]
    assert "artifacts" not in trace.model_dump()
    assert "evidence_artifact_ids" not in trace.gate_results[0].model_dump()
    assert set(result.assessment.evidence_paths) <= {
        path for gate in trace.gate_results for path in gate.evidence_paths
    }
    assert artifact_count == 0


def test_record_validation_report_passed_writes_report_and_closes_failure(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    created = task_service.create_task(message="Validate motor code locally.").task
    task = _with_open_test_failure(created)
    TaskRepository(db_session).update_task_state(task)
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=Path(task.workspace.root),
        )
    )

    result = service.record_validation_report(
        task.task_id,
        validation_type="test",
        status="passed",
        summary="Local fallback simulation passed.",
        read_paths=["src/plc_code.st"],
        failure_ids=["failure-test-001"],
        details={"passed_cases": 3},
        command="python tests/plc_sim.py",
    )
    updated = TaskRepository(db_session).get_task(task.task_id)
    report_path = result.report_paths[0]
    report = json.loads(
        (Path(task.workspace.root) / report_path).read_text(encoding="utf-8")
    )

    assert result.status == "applied"
    assert result.details["resolved_failure_ids"] == ["failure-test-001"]
    assert report["kind"] == "main_agent_validation_report"
    assert report["validation_type"] == "test"
    assert report["status"] == "passed"
    assert report["resolved_failure_ids"] == ["failure-test-001"]
    assert updated.current_files.latest_test_report == report_path
    assert report_path in updated.current_files.all_paths
    assert updated.gates.test_required is True
    assert updated.gates.latest_test_passed is True
    assert updated.gates.regression_required is False
    assert updated.gates.has_blocking_failure is False
    assert updated.failures[0].status == "resolved"
    assert updated.failures[0].resolved_by_path == report_path

    gate_result = QualityGateService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).run_quality_gate(task.task_id)
    test_gate = gate_result.assessment.outcome_for("test_gate")
    assert test_gate.message == "Latest test report passed."
    assert test_gate.evidence_paths == (report_path,)


def test_record_validation_report_failed_does_not_close_failure(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    created = task_service.create_task(message="Validate motor code locally.").task
    task = _with_open_test_failure(created)
    TaskRepository(db_session).update_task_state(task)
    service = AgentToolService(
        AgentToolContext(
            session=db_session,
            artifact_root=tmp_path / "artifacts",
            workspace_root=Path(task.workspace.root),
        )
    )

    result = service.record_validation_report(
        task.task_id,
        validation_type="test",
        status="failed",
        summary="Local fallback simulation still failed.",
        read_paths=["src/plc_code.st"],
        failure_ids=["failure-test-001"],
    )
    updated = TaskRepository(db_session).get_task(task.task_id)
    report_path = result.report_paths[0]

    assert result.status == "applied"
    assert result.details["resolved_failure_ids"] == []
    assert (Path(task.workspace.root) / report_path).is_file()
    assert updated.current_files.latest_test_report == (
        ".router/reports/worker-job-test-failed/test_report.json"
    )
    assert report_path in updated.current_files.all_paths
    assert updated.gates.latest_test_passed is False
    assert updated.gates.has_blocking_failure is True
    assert updated.failures[0].status == "open"
    assert updated.failures[0].resolved_by_path is None


def test_main_agent_finalizes_without_forced_quality_gate(
    db_session: Session,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task = task_service.create_task(message="Answer a PLC question.").task
    TaskRepository(db_session).update_task_state(
        task.model_copy(
            deep=True,
            update={
                "status": TaskStatus.RUNNING,
                "phase": TaskPhase.PLANNING,
                "task_type": "qa",
            },
        )
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=FinalOnlyRunner(),
    )

    output = service.run_episode(task.task_id)
    updated = TaskRepository(db_session).get_task(task.task_id)
    events = EventService(db_session).list_visible_events(task.task_id)
    artifact_count = db_session.scalar(select(func.count()).select_from(ArtifactRow))

    assert output.final_task_status == "succeeded"
    assert updated.status == "succeeded"
    assert updated.current_files.final_report is not None
    assert updated.current_files.latest_gate_report is None
    assert "agent.stop_blocked" not in [event.type for event in events]
    assert artifact_count == 0


def test_delete_task_removes_generated_workspace(
    db_session: Session,
    task_service: TaskService,
) -> None:
    task = task_service.create_task(message="Create pump logic.").task
    workspace_root = Path(task.workspace.root)

    task_service.delete_task(task.task_id)

    assert not workspace_root.exists()


def _with_open_test_failure(task: TaskState) -> TaskState:
    base = _with_files(
        task,
        current_code="src/plc_code.st",
        requirements=".router/requests/requirements.json",
    )
    test_report = ".router/reports/worker-job-test-failed/test_report.json"
    current_files = base.current_files.model_copy(
        update={
            "latest_test_report": test_report,
            "all_paths": _dedupe_paths(
                [
                    base.current_files.raw_user_request,
                    base.current_files.requirements,
                    base.current_files.current_code,
                    test_report,
                ]
            ),
        }
    )
    return base.model_copy(
        deep=True,
        update={
            "phase": TaskPhase.REPAIRING,
            "current_files": current_files,
            "gates": base.gates.model_copy(
                update={
                    "test_required": True,
                    "regression_required": True,
                    "latest_test_passed": False,
                    "has_blocking_failure": True,
                }
            ),
            "failures": [
                Failure(
                    failure_id="failure-test-001",
                    source=FailureSource.TEST,
                    severity=Severity.BLOCKING,
                    title="Motor safety test failed",
                    description="The previous PLC test report found a blocking failure.",
                    evidence_paths=[test_report],
                    status=FailureStatus.OPEN,
                    created_by_worker_job_id="worker-job-test-failed",
                    created_at=utc_now(),
                )
            ],
        },
    )


def _with_files(
    task: TaskState,
    *,
    current_code: str,
    requirements: str | None = None,
) -> TaskState:
    current_files = task.current_files.model_copy(
        update={
            "current_code": current_code,
            "requirements": requirements,
            "all_paths": [
                path for path in [requirements, current_code] if path is not None
            ],
        }
    )
    return task.model_copy(
        deep=True,
        update={
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "task_type": "new_plc_development",
            "current_files": current_files,
            "gates": GateState(
                test_required=True,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=None,
                latest_formal_passed=None,
                has_blocking_failure=False,
                can_finish_as_success=False,
            ),
        },
    )


def _dedupe_paths(paths: list[str | None]) -> list[str]:
    output: list[str] = []
    for path in paths:
        if path is None or path in output:
            continue
        output.append(path)
    return output


class FinalOnlyRunner:
    def run_orchestration(
        self,
        *,
        agent: object,
        input_text: str,
        context: object,
        max_turns: int,
        run_config: object,
    ) -> MainAgentEpisodeOutput:
        _ = (agent, input_text, context, max_turns)
        task_id = str(getattr(run_config, "group_id"))
        return MainAgentEpisodeOutput(
            task_id=task_id,
            main_agent_run_id="main-agent-run-file-centric",
            final_task_status="succeeded",
            phase="completed",
            gate_summary=MainAgentGateSummary(
                test_required=False,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=None,
                latest_formal_passed=None,
                has_blocking_failure=False,
                can_finish_as_success=True,
            ),
            next_recommended_action="none",
            summary="Answered without running an implicit quality gate.",
        )
