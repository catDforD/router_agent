from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.main_agent import MainAgentService, build_main_agent_event
from app.core.errors import RepositoryNotFoundError
from app.core.time import utc_now
from app.mcp.adapter import McpAdapter
from app.models.db_models import Base
from app.models.router_schema import (
    ArtifactCreatorType,
    ArtifactType,
    ArtifactVisibility,
    EventType,
    WorkerType,
)
from app.repositories.gate_repo import GateResultRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.services.task_service import TaskService
from app.services.trace_summary import TraceSummaryService
from app.workers.worker_input_builder import build_worker_input


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


def test_trace_summary_reconstructs_task_execution_without_content(
    db_session: Session,
    tmp_path: Path,
) -> None:
    secret = "TRACE-SUMMARY-SECRET-CONTENT"
    artifact_root = tmp_path / "artifacts"
    task = TaskService(
        session=db_session,
        artifact_root=artifact_root,
    ).create_task(message="Create motor logic.").task
    started = MainAgentService(
        session=db_session,
        artifact_root=artifact_root,
    ).start_main_agent_run(task.task_id)

    final_report = ArtifactStore(
        session=db_session,
        artifact_root=artifact_root,
    ).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.FINAL_REPORT,
            version=1,
            name="final_report.json",
            content={"secret": secret},
            summary="Final report summary.",
            visibility=ArtifactVisibility.USER,
            created_by={
                "type": ArtifactCreatorType.MAIN_AGENT,
                "id": started.trace.latest_main_agent_run_id,
                "main_agent_run_id": started.trace.latest_main_agent_run_id,
            },
            metadata={"tags": ["final_report"]},
            mime_type="application/json",
        )
    ).artifact
    replay_log = ArtifactStore(
        session=db_session,
        artifact_root=artifact_root,
    ).write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.MAIN_AGENT_LOG,
            version=1,
            name="main_agent_log.json",
            content={"secret": secret},
            summary="Replay log summary.",
            visibility=ArtifactVisibility.INTERNAL,
            created_by={
                "type": ArtifactCreatorType.MAIN_AGENT,
                "id": started.trace.latest_main_agent_run_id,
                "main_agent_run_id": started.trace.latest_main_agent_run_id,
            },
            metadata={"tags": ["main_agent_log"]},
            mime_type="application/json",
        )
    ).artifact
    EventService(db_session).append_event(
        build_main_agent_event(
            task_id=task.task_id,
            event_type=EventType.MAIN_AGENT_COMPLETED,
            title="Main Agent completed",
            message="Done.",
            openai_trace_id=started.trace.openai_trace_id,
            main_agent_run_id=started.trace.latest_main_agent_run_id,
            artifact_ids=[final_report.artifact_id, replay_log.artifact_id],
            payload={
                "task_id": task.task_id,
                "main_agent_run_id": started.trace.latest_main_agent_run_id,
                "final_report_artifact_id": final_report.artifact_id,
                "main_agent_log_artifact_id": replay_log.artifact_id,
            },
            created_at=utc_now(),
        )
    )

    worker_input = build_worker_input(
        TaskRepository(db_session).get_task(task.task_id),
        WorkerType.PLC_DEV,
    )
    worker_result = McpAdapter(
        session=db_session,
        artifact_root=artifact_root,
    ).call_worker(worker_input)
    GateResultRepository(db_session).create_result(
        task_id=task.task_id,
        gate_type="code_gate",
        status="passed",
        blocking=False,
        evidence_artifact_ids=[
            artifact.artifact_id for artifact in worker_result.produced_artifacts
        ],
        result={"reason": "code produced"},
        created_at=utc_now(),
        gate_result_id="gate-result-trace-001",
    )

    summary = TraceSummaryService(db_session).get_task_trace_summary(task.task_id)
    payload = summary.model_dump_json()

    assert summary.task_id == task.task_id
    assert summary.openai_trace_id == started.trace.openai_trace_id
    assert summary.main_agent_run_ids == [started.trace.latest_main_agent_run_id]
    assert summary.main_agent_runs[0].started_event_id is not None
    assert summary.main_agent_runs[0].completed_event_id is not None
    assert summary.main_agent_runs[0].final_report_artifact_id == final_report.artifact_id
    assert summary.main_agent_runs[0].replay_log_artifact_id == replay_log.artifact_id
    assert summary.worker_jobs[0].worker_job_id == worker_input.worker_job_id
    assert set(summary.worker_jobs[0].produced_artifact_ids) == {
        artifact.artifact_id for artifact in worker_result.produced_artifacts
    }
    assert summary.gate_results[0].gate_result_id == "gate-result-trace-001"
    assert [event.seq for event in summary.events] == sorted(
        event.seq for event in summary.events
    )
    assert all("inline_content" not in artifact.model_dump() for artifact in summary.artifacts)
    assert secret not in payload


def test_trace_summary_missing_task_raises_not_found(
    db_session: Session,
) -> None:
    with pytest.raises(RepositoryNotFoundError):
        TraceSummaryService(db_session).get_task_trace_summary("missing-task")
