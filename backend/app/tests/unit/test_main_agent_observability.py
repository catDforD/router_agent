import json
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.output_schema import (
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
)
from app.models.db_models import ArtifactRow, Base, EventRow
from app.models.router_schema import TaskState
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactStore


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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
def task(db_session: Session) -> TaskState:
    payload = json.loads(
        (FIXTURE_DIR / "task_state.valid.json").read_text(encoding="utf-8")
    )
    task_state = TaskState.model_validate(payload)
    TaskRepository(db_session).create_task(task_state)
    return task_state


def recorder(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
    checkpoints: list[str] | None = None,
) -> MainAgentObservabilityRecorder:
    return MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
        openai_trace_id="trace-001",
        checkpoint=(lambda: checkpoints.append("checkpoint")) if checkpoints is not None else None,
    )


def test_recorder_appends_turn_and_tool_events(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    checkpoints: list[str] = []
    observed = recorder(db_session, tmp_path, task, checkpoints)

    turn = observed.start_turn()
    event = observed.record_tool_call(
        tool_name="call_plc_dev",
        arguments={
            "task_id": task.task_id,
            "api_key": "secret",
            "objective": "Generate code",
        },
        rationale_summary="Use plc-dev because no current PLC code exists.",
        input_artifact_ids=["artifact-raw-request-001"],
    )

    rows = list(db_session.execute(select(EventRow).order_by(EventRow.seq)).scalars())

    assert turn == 1
    assert [row.type for row in rows] == [
        "main_agent.turn_started",
        "main_agent.tool_called",
    ]
    assert event.payload["turn_index"] == 1
    assert event.payload["arguments"]["api_key"] == "[redacted]"
    assert event.correlation.openai_trace_id == "trace-001"
    assert event.correlation.main_agent_run_id == "main-agent-run-001"
    assert checkpoints == ["checkpoint", "checkpoint"]


def test_recorder_truncates_rationale_and_records_tool_result(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    observed = recorder(db_session, tmp_path, task)
    observed.start_turn()
    observed.record_tool_call(
        tool_name="call_plc_test",
        rationale_summary="x" * 1200,
    )
    result_event = observed.record_tool_result(
        tool_name="call_plc_test",
        result={
            "status": "applied",
            "summary": "PLC tests passed.",
            "artifact_refs": [
                {
                    "artifact_id": "artifact-test-report-001",
                    "type": "test_report",
                    "version": 1,
                }
            ],
            "failures": [{"failure_id": "failure-001"}],
            "worker_job_id": "worker-job-001",
            "next_recommended_action": "run_quality_gate",
        },
    )

    call_row = db_session.execute(
        select(EventRow).where(EventRow.type == "main_agent.tool_called")
    ).scalar_one()

    assert call_row.event_json["payload"]["rationale_summary"].endswith(
        "... [truncated]"
    )
    assert result_event.payload["artifact_ids"] == ["artifact-test-report-001"]
    assert result_event.payload["failure_ids"] == ["failure-001"]
    assert result_event.correlation.artifact_ids == ["artifact-test-report-001"]
    assert result_event.correlation.failure_ids == ["failure-001"]


def test_recorder_writes_report_log_and_completed_event(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    observed = recorder(db_session, tmp_path, task)
    output = MainAgentEpisodeOutput(
        task_id=task.task_id,
        main_agent_run_id="main-agent-run-001",
        final_task_status="succeeded",
        phase="completed",
        gate_summary=MainAgentGateSummary(
            test_required=True,
            formal_required=False,
            regression_required=False,
            formal_regression_required=False,
            latest_test_passed=True,
            latest_formal_passed=None,
            has_blocking_failure=False,
            can_finish_as_success=True,
        ),
        next_recommended_action="none",
        summary="Task completed.",
    )

    observed.start_turn()
    final_report = observed.write_final_report(output)
    replay_log = observed.write_replay_log(final_output=output)
    completed = observed.record_completed(
        output=output,
        final_report=final_report,
        replay_log=replay_log,
    )
    stored_report = ArtifactStore(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).read_artifact_content(final_report.artifact_id)
    artifact_rows = list(
        db_session.execute(select(ArtifactRow).order_by(ArtifactRow.created_at)).scalars()
    )
    restored = TaskRepository(db_session).get_task(task.task_id)

    assert [row.type for row in artifact_rows] == ["final_report", "main_agent_log"]
    assert json.loads(stored_report.content)["output"]["summary"] == "Task completed."
    assert completed.type == "main_agent.completed"
    assert completed.correlation.artifact_ids == [
        final_report.artifact_id,
        replay_log.artifact_id,
    ]
    assert restored.current_artifacts.final_report is not None
    assert restored.current_artifacts.final_report.artifact_id == final_report.artifact_id
