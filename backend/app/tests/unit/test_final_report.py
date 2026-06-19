import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.output_schema import (
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
    MainAgentPlanStep,
)
from app.core.time import utc_now
from app.models.db_models import Base
from app.models.router_schema import (
    Failure,
    FailureReproduction,
    GateState,
    Severity,
    TaskPhase,
    TaskStatus,
    TaskState,
)
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.final_report import build_final_report_payload


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
FULL_CODE_SENTINEL = "FULL_CODE_BODY_SHOULD_NOT_APPEAR"
FULL_REPORT_SENTINEL = "FULL_TEST_REPORT_SHOULD_NOT_APPEAR"


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


def test_final_report_payload_includes_stable_delivery_sections(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _prepare_success_task(db_session, tmp_path, task.task_id)
    output = _episode_output(task.task_id, final_task_status="succeeded")

    report = build_final_report_payload(
        session=db_session,
        task_id=task.task_id,
        output=output,
        main_agent_run_id="main-agent-run-001",
        created_at=utc_now(),
    )

    assert report["kind"] == "main_agent_final_report"
    assert report["schema_version"] == "router.v1"
    assert report["report_version"] == 1
    assert report["task_id"] == task.task_id
    assert report["main_agent_run_id"] == "main-agent-run-001"
    assert report["final_task_status"] == "succeeded"
    assert report["user_goal"]["raw_user_request"] == task.raw_user_request
    assert report["classification"]["task_type"] == "new_plc_development"
    assert report["delivery_artifacts"]["final_plc_code"]["artifact_id"] == (
        "artifact-code-v1"
    )
    assert report["delivery_artifacts"]["test_report"]["artifact_id"] == (
        "artifact-test-report-v1"
    )
    assert report["delivery_artifacts"]["gate_report"]["artifact_id"] == (
        "artifact-gate-report-v1"
    )
    assert report["validation_summary"]["latest_test_passed"] is True
    assert report["validation_summary"]["gate_results"][0]["gate_type"] == "final_gate"
    assert report["repair_summary"]["repair_rounds"] == 0
    assert report["unresolved_items"]["blocking_failure_count"] == 0
    assert report["main_agent_output_summary"]["next_recommended_action"] == "none"
    assert report["plan"][0]["tool_name"] == "run_quality_gate"
    assert report["decisions"][0]["summary"] == "Quality Gate passed."


def test_final_report_payload_records_repair_evidence(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _prepare_success_task(db_session, tmp_path, task.task_id)
    patch = _write_artifact(
        db_session,
        tmp_path,
        task_id=task.task_id,
        artifact_type="patch",
        artifact_id="artifact-patch-v1",
        content="diff --git a/program.st b/program.st\n",
        summary="Repair patch.",
    )
    repair = _write_artifact(
        db_session,
        tmp_path,
        task_id=task.task_id,
        artifact_type="repair_summary",
        artifact_id="artifact-repair-summary-v1",
        content={"summary": "Repaired emergency stop handling."},
        summary="Repair summary.",
    )
    current = TaskRepository(db_session).get_task(task.task_id)
    updated = current.model_copy(
        deep=True,
        update={
            "runtime_limits": current.runtime_limits.model_copy(
                update={"repair_rounds": 1}
            ),
            "failures": [
                _failure(
                    status="resolved",
                    evidence_artifact_ids=["artifact-test-report-v1"],
                    resolved_by_artifact_id=patch,
                )
            ],
        },
    )
    TaskRepository(db_session).update_task_state(updated)

    report = build_final_report_payload(
        session=db_session,
        task_id=task.task_id,
        output=_episode_output(task.task_id, final_task_status="succeeded"),
        main_agent_run_id="main-agent-run-001",
        created_at=utc_now(),
    )

    assert report["repair_summary"]["repair_rounds"] == 1
    assert report["repair_summary"]["resolved_failure_count"] == 1
    assert report["repair_summary"]["latest_patch"]["artifact_id"] == patch
    assert report["repair_summary"]["latest_repair_summary"]["artifact_id"] == repair
    assert report["repair_summary"]["failures"][0]["status"] == "resolved"


def test_final_report_payload_records_partial_failure_unresolved_items(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _prepare_success_task(db_session, tmp_path, task.task_id)
    current = TaskRepository(db_session).get_task(task.task_id)
    updated = current.model_copy(
        deep=True,
        update={
            "gates": current.gates.model_copy(
                update={
                    "has_blocking_failure": True,
                    "can_finish_as_success": False,
                }
            ),
            "runtime_limits": current.runtime_limits.model_copy(
                update={"repair_rounds": 3}
            ),
            "failures": [
                _failure(
                    status="open",
                    evidence_artifact_ids=["artifact-test-report-v1"],
                )
            ],
        },
    )
    TaskRepository(db_session).update_task_state(updated)

    report = build_final_report_payload(
        session=db_session,
        task_id=task.task_id,
        output=_episode_output(task.task_id, final_task_status="partial_failed"),
        main_agent_run_id="main-agent-run-001",
        created_at=utc_now(),
    )

    assert report["final_task_status"] == "partial_failed"
    assert report["repair_summary"]["repair_budget_exhausted"] is True
    assert report["repair_summary"]["open_failure_count"] == 1
    assert report["unresolved_items"]["blocking_failure_count"] == 1
    assert report["unresolved_items"]["open_failures"][0]["failure_id"] == "failure-001"


def test_final_report_payload_allows_missing_optional_artifacts(
    db_session: Session,
    task: TaskState,
) -> None:
    output = _episode_output(task.task_id, final_task_status="failed")

    report = build_final_report_payload(
        session=db_session,
        task_id=task.task_id,
        output=output,
        main_agent_run_id="main-agent-run-001",
        created_at=utc_now(),
    )

    assert report["delivery_artifacts"]["final_plc_code"] is None
    assert report["delivery_artifacts"]["test_report"] is None
    assert report["delivery_artifacts"]["all"] == [
        {"artifact_id": "artifact-raw-request-001"}
    ]
    assert report["final_task_status"] == "failed"


def test_final_report_payload_does_not_inline_large_artifact_content(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    _prepare_success_task(db_session, tmp_path, task.task_id)

    report = build_final_report_payload(
        session=db_session,
        task_id=task.task_id,
        output=_episode_output(task.task_id, final_task_status="succeeded"),
        main_agent_run_id="main-agent-run-001",
        created_at=utc_now(),
    )
    serialized = json.dumps(report)

    assert FULL_CODE_SENTINEL not in serialized
    assert FULL_REPORT_SENTINEL not in serialized
    assert "artifact-code-v1" in serialized
    assert "artifact-test-report-v1" in serialized


def _prepare_success_task(db_session: Session, tmp_path: Path, task_id: str) -> None:
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="raw_user_request",
        artifact_id="artifact-raw-request-001",
        content={"message": "Create a PLC program for a pump interlock."},
        summary="Original user request.",
    )
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="requirements_ir",
        artifact_id="artifact-requirements-v1",
        content={"goal": "Pump interlock"},
        summary="Requirements IR.",
    )
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="io_contract",
        artifact_id="artifact-io-contract-v1",
        content={"inputs": ["StartCmd"], "outputs": ["PumpRun"]},
        summary="I/O contract.",
    )
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="plc_code",
        artifact_id="artifact-code-v1",
        content=f"PumpRun := StartCmd; {FULL_CODE_SENTINEL}",
        summary="Final PLC code.",
    )
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="test_report",
        artifact_id="artifact-test-report-v1",
        content={"body": FULL_REPORT_SENTINEL, "status": "passed"},
        summary="Passing test report.",
    )
    _write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type="gate_report",
        artifact_id="artifact-gate-report-v1",
        content={"status": "passed"},
        summary="Quality Gate passed.",
    )
    current = TaskRepository(db_session).get_task(task_id)
    updated = current.model_copy(
        deep=True,
        update={
            "status": TaskStatus.RUNNING.value,
            "phase": TaskPhase.QUALITY_GATE.value,
            "gates": GateState(
                test_required=True,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=True,
                latest_formal_passed=None,
                has_blocking_failure=False,
                can_finish_as_success=True,
            ),
        },
    )
    TaskRepository(db_session).update_task_state(updated)
    GateResultRepository(db_session).create_result(
        task_id=task_id,
        gate_type="final_gate",
        status="passed",
        blocking=False,
        evidence_artifact_ids=["artifact-gate-report-v1"],
        result={
            "aggregate_status": "passed",
            "message": "Quality Gate passed.",
        },
        created_at=utc_now(),
    )


def _write_artifact(
    db_session: Session,
    tmp_path: Path,
    *,
    task_id: str,
    artifact_type: str,
    artifact_id: str,
    content: Any,
    summary: str,
) -> str:
    store = ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")
    result = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task_id,
            artifact_type=artifact_type,
            version=1,
            name=f"{artifact_type}.json",
            content=content,
            summary=summary,
            artifact_id=artifact_id,
            mime_type="application/json",
        )
    )
    return result.artifact.artifact_id


def _episode_output(task_id: str, *, final_task_status: str) -> MainAgentEpisodeOutput:
    return MainAgentEpisodeOutput(
        task_id=task_id,
        main_agent_run_id="main-agent-run-001",
        final_task_status=final_task_status,
        phase="completed",
        decisions=[
            MainAgentDecision(
                decision_type="quality_gate",
                summary="Quality Gate passed.",
                action="finish",
            )
        ],
        plan=[
            MainAgentPlanStep(
                order=1,
                action="Run Quality Gate",
                status="completed",
                tool_name="run_quality_gate",
            )
        ],
        gate_summary=MainAgentGateSummary(
            test_required=True,
            formal_required=False,
            regression_required=False,
            formal_regression_required=False,
            latest_test_passed=True,
            latest_formal_passed=None,
            has_blocking_failure=final_task_status == "partial_failed",
            can_finish_as_success=final_task_status == "succeeded",
        ),
        next_recommended_action="none",
        summary="Final report summary.",
    )


def _failure(
    *,
    status: str,
    evidence_artifact_ids: list[str],
    resolved_by_artifact_id: str | None = None,
) -> Failure:
    now = utc_now()
    return Failure(
        failure_id="failure-001",
        source="test",
        severity=Severity.BLOCKING,
        title="Pump interlock test failed",
        description="Pump output did not follow the interlock condition.",
        reproduction=FailureReproduction(
            input_trace_artifact_id=evidence_artifact_ids[0],
        ),
        evidence_artifact_ids=evidence_artifact_ids,
        status=status,
        created_by_worker_job_id="worker-job-test-001",
        resolved_by_worker_job_id=(
            "worker-job-repair-001" if status == "resolved" else None
        ),
        resolved_by_artifact_id=resolved_by_artifact_id,
        created_at=now,
        resolved_at=now if status == "resolved" else None,
    )
