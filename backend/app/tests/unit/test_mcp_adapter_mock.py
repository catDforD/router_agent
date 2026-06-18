import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.mcp.adapter import McpAdapter
from app.mcp.client import PlcMcpConnectionError, PlcMcpTimeoutError
from app.mcp.draft import LlmWorkerDraftOutput, McpWorkerRequest
from app.mcp.mock_worker import SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED
from app.mcp.normalizer import (
    ERROR_MCP_CONNECTION_ERROR,
    ERROR_MCP_TIMEOUT,
    ERROR_WORKER_SCHEMA_INVALID,
)
from app.models.db_models import Base
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ExpectedOutputSpec,
    TaskState,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService


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
    task_state = TaskState.model_validate(load_fixture("task_state.valid.json"))
    running = task_state.model_copy(
        deep=True,
        update={
            "status": "running",
            "phase": "planning",
            "event_seq": 0,
        },
    )
    TaskRepository(db_session).create_task(running)
    return running


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def artifact_ref(
    artifact_id: str,
    artifact_type: ArtifactType | str,
    *,
    version: int = 1,
) -> ArtifactRef:
    artifact_type_value = (
        artifact_type.value if isinstance(artifact_type, ArtifactType) else artifact_type
    )
    return ArtifactRef(
        artifact_id=artifact_id,
        type=artifact_type_value,
        version=version,
        uri=f"local://artifacts/task-001/{artifact_id}",
        summary=f"{artifact_type_value} artifact",
    )


def raw_ref() -> ArtifactRef:
    return artifact_ref("artifact-raw-request-001", ArtifactType.RAW_USER_REQUEST)


def requirements_ref() -> ArtifactRef:
    return artifact_ref("artifact-requirements-001", ArtifactType.REQUIREMENTS_IR)


def code_ref(version: int = 1) -> ArtifactRef:
    return artifact_ref(f"artifact-code-{version:03d}", ArtifactType.PLC_CODE, version=version)


def report_ref() -> ArtifactRef:
    return artifact_ref("artifact-test-report-001", ArtifactType.TEST_REPORT)


def worker_input(
    task: TaskState,
    worker_type: WorkerType | str,
    input_artifacts: list[ArtifactRef],
    *,
    worker_job_id: str | None = None,
) -> WorkerInput:
    worker = worker_type.value if isinstance(worker_type, WorkerType) else worker_type
    job_id = worker_job_id or f"worker-job-{worker.replace('-', '-')}-001"
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=job_id,
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode=worker_mode(worker),
        objective=f"Run mock {worker}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.normalized_goal or task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=task.runtime_limits.repair_rounds,
            assumptions=[],
        ),
        constraints=[],
        expected_outputs=expected_outputs(worker),
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=job_id),
        idempotency_key=f"{task.task_id}:{job_id}",
        created_at=task.created_at,
    )


def worker_mode(worker_type: str) -> WorkerMode:
    return {
        WorkerType.PLC_DEV.value: WorkerMode.CREATE,
        WorkerType.PLC_TEST.value: WorkerMode.TEST,
        WorkerType.PLC_FORMAL.value: WorkerMode.FORMAL_VERIFY,
        WorkerType.PLC_REPAIR.value: WorkerMode.REPAIR,
    }[worker_type]


def expected_outputs(worker_type: str) -> list[ExpectedOutputSpec]:
    output_types = {
        WorkerType.PLC_DEV.value: [
            ArtifactType.REQUIREMENTS_IR,
            ArtifactType.PLC_CODE,
            ArtifactType.IO_CONTRACT,
        ],
        WorkerType.PLC_TEST.value: [ArtifactType.TEST_REPORT],
        WorkerType.PLC_FORMAL.value: [ArtifactType.FORMAL_REPORT],
        WorkerType.PLC_REPAIR.value: [
            ArtifactType.PATCH,
            ArtifactType.PLC_CODE,
            ArtifactType.REPAIR_SUMMARY,
        ],
    }[worker_type]
    return [
        ExpectedOutputSpec(
            artifact_type=artifact_type,
            required=True,
            description=f"Mock {artifact_type.value} output.",
        )
        for artifact_type in output_types
    ]


def adapter(db_session: Session, tmp_path: Path, *, mock_runner: Any | None = None) -> McpAdapter:
    return McpAdapter(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        mock_runner=mock_runner,
    )


def artifact_types(result_artifacts: list[ArtifactRef]) -> set[str]:
    return {str(artifact.type) for artifact in result_artifacts}


def visible_event_types(db_session: Session, task_id: str) -> list[str]:
    return [
        str(event.type)
        for event in EventService(db_session).list_visible_events(task_id)
    ]


def test_plc_dev_returns_completed_result_and_persists_requirements_and_code(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = adapter(db_session, tmp_path).call_worker(payload)

    assert result.execution_status == "completed"
    assert result.worker_type == "plc-dev"
    assert {ArtifactType.REQUIREMENTS_IR.value, ArtifactType.PLC_CODE.value} <= (
        artifact_types(result.produced_artifacts)
    )
    stored = ArtifactStore(db_session, tmp_path / "artifacts").list_task_artifacts(task.task_id)
    assert {str(artifact.type) for artifact in stored} >= {
        ArtifactType.REQUIREMENTS_IR.value,
        ArtifactType.PLC_CODE.value,
    }


def test_plc_test_pass_returns_test_report_and_metrics(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_TEST,
        [requirements_ref(), code_ref()],
    )

    result = adapter(db_session, tmp_path).call_worker(payload)

    assert result.outcome.status == "passed"
    assert artifact_types(result.produced_artifacts) == {ArtifactType.TEST_REPORT.value}
    assert result.metrics.test_metrics is not None
    assert result.metrics.test_metrics.failed == 0


def test_plc_test_failure_returns_report_trace_and_blocking_failure(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_TEST,
        [requirements_ref(), code_ref(version=1)],
    )

    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario="test_failed_then_repair_pass",
    )

    assert result.outcome.status == "failed"
    assert result.outcome.blocking is True
    assert artifact_types(result.produced_artifacts) == {
        ArtifactType.TEST_REPORT.value,
        ArtifactType.FAILING_TRACE.value,
    }
    assert len(result.failures) == 1
    assert result.failures[0].severity == "blocking"
    assert len(result.failures[0].evidence_artifact_ids) == 2
    assert result.next_recommended_action == "repair"


def test_exhausted_repair_scenario_keeps_repaired_code_failing(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_TEST,
        [requirements_ref(), code_ref(version=2)],
    )

    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario=SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    )

    assert result.outcome.status == "failed"
    assert result.outcome.blocking is True
    assert artifact_types(result.produced_artifacts) == {
        ArtifactType.TEST_REPORT.value,
        ArtifactType.FAILING_TRACE.value,
    }
    assert {artifact.version for artifact in result.produced_artifacts} == {2}
    assert len(result.failures) == 1
    assert result.next_recommended_action == "repair"


def test_plc_formal_failure_returns_report_counterexample_and_blocking_failure(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_FORMAL,
        [requirements_ref(), code_ref(version=1)],
    )

    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario="formal_failed_then_repair_pass",
    )

    assert result.outcome.status == "failed"
    assert artifact_types(result.produced_artifacts) == {
        ArtifactType.FORMAL_REPORT.value,
        ArtifactType.COUNTEREXAMPLE.value,
    }
    assert len(result.failures) == 1
    assert result.failures[0].source == "formal"
    assert result.failures[0].reproduction is not None
    assert result.failures[0].reproduction.counterexample_artifact_id is not None


def test_plc_repair_returns_patch_summary_and_patched_code_v2(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(
        task,
        WorkerType.PLC_REPAIR,
        [code_ref(version=1), report_ref()],
    )

    result = adapter(db_session, tmp_path).call_worker(payload)

    assert result.outcome.status == "passed"
    assert artifact_types(result.produced_artifacts) == {
        ArtifactType.PATCH.value,
        ArtifactType.PLC_CODE.value,
        ArtifactType.REPAIR_SUMMARY.value,
    }
    patched_code = [
        artifact
        for artifact in result.produced_artifacts
        if artifact.type == ArtifactType.PLC_CODE.value
    ][0]
    assert patched_code.version == 2


def test_timeout_normalization_returns_timeout_result_and_event(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = adapter(db_session, tmp_path).call_worker(
        payload,
        scenario="worker_timeout",
    )

    assert result.execution_status == "timeout"
    assert result.error is not None
    assert result.error.error_code == ERROR_MCP_TIMEOUT
    assert result.error.retryable is True
    assert "worker.timeout" in visible_event_types(db_session, task.task_id)
    job = WorkerJobRepository(db_session).get_job(payload.worker_job_id)
    assert job.status == "timeout"


def test_successful_invocation_records_worker_job_and_user_visible_events(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = adapter(db_session, tmp_path).call_worker(payload)
    job = WorkerJobRepository(db_session).get_job(payload.worker_job_id)
    events = visible_event_types(db_session, task.task_id)

    assert job.status == "completed"
    assert job.input == payload
    assert job.result == result
    assert events[0] == "worker.started"
    assert events.count("artifact.created") == len(result.produced_artifacts)
    assert events[-1] == "worker.completed"


def test_invalid_worker_output_uses_schema_invalid_path(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    def invalid_runner(worker_input: WorkerInput, *, scenario: str) -> object:
        return {"invalid": True}

    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = adapter(db_session, tmp_path, mock_runner=invalid_runner).call_worker(payload)

    assert result.execution_status == "error"
    assert result.error is not None
    assert result.error.error_code == ERROR_WORKER_SCHEMA_INVALID
    assert result.produced_artifacts == []
    assert WorkerJobRepository(db_session).get_job(payload.worker_job_id).status == "error"
    assert "worker.error" in visible_event_types(db_session, task.task_id)


def test_real_mcp_dispatch_persists_draft_artifacts_and_trace(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    fake_client = FakeRealMcpClient(real_dev_draft())
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = real_adapter(db_session, tmp_path, fake_client).call_worker(payload)
    job = WorkerJobRepository(db_session).get_job(payload.worker_job_id)

    assert fake_client.calls[0][0] == "plc_dev.run"
    assert fake_client.calls[0][1].worker_input.trace_context.mcp_request_id is not None
    assert result.trace_context.mcp_request_id == job.input.trace_context.mcp_request_id
    assert result.execution_status == "completed"
    assert artifact_types(result.produced_artifacts) == {
        ArtifactType.REQUIREMENTS_IR.value,
        ArtifactType.PLC_CODE.value,
        ArtifactType.IO_CONTRACT.value,
    }
    assert job.status == "completed"
    assert "worker.completed" in visible_event_types(db_session, task.task_id)


def test_hybrid_mode_routes_real_and_mock_workers(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    fake_client = FakeRealMcpClient(real_dev_draft())
    hybrid = real_adapter(
        db_session,
        tmp_path,
        fake_client,
        mcp_mode="hybrid",
        plc_dev_mode="real",
        plc_test_mode="mock",
    )

    dev_result = hybrid.call_worker(worker_input(task, WorkerType.PLC_DEV, [raw_ref()]))
    test_result = hybrid.call_worker(
        worker_input(
            task,
            WorkerType.PLC_TEST,
            [requirements_ref(), code_ref()],
        )
    )

    assert len(fake_client.calls) == 1
    assert fake_client.calls[0][0] == "plc_dev.run"
    assert artifact_types(dev_result.produced_artifacts) == {
        ArtifactType.REQUIREMENTS_IR.value,
        ArtifactType.PLC_CODE.value,
        ArtifactType.IO_CONTRACT.value,
    }
    assert artifact_types(test_result.produced_artifacts) == {ArtifactType.TEST_REPORT.value}


def test_real_mcp_request_includes_bounded_artifact_content(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    store = ArtifactStore(db_session, tmp_path / "artifacts")
    stored = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.RAW_USER_REQUEST,
            version=1,
            name="raw.txt",
            content="abcdef",
            summary="Raw request.",
        )
    ).artifact
    raw_artifact_ref = store.get_artifact_ref(stored.artifact_id)
    fake_client = FakeRealMcpClient(real_dev_draft())
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_artifact_ref])

    real_adapter(
        db_session,
        tmp_path,
        fake_client,
        plc_worker_artifact_max_chars=3,
    ).call_worker(payload)

    snapshot = fake_client.calls[0][1].input_artifacts[0]
    assert snapshot.content == "abc"
    assert snapshot.content_truncated is True
    assert snapshot.content_chars == 3


def test_real_mcp_invalid_draft_uses_schema_invalid_path(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    fake_client = FakeRealMcpClient(
        LlmWorkerDraftOutput.model_validate(
            {
                "outcome": {"status": "passed", "blocking": False},
                "summary": "Passed without artifacts.",
                "artifact_writes": [],
                "next_recommended_action": "none",
            }
        )
    )
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = real_adapter(db_session, tmp_path, fake_client).call_worker(payload)

    assert result.execution_status == "error"
    assert result.error is not None
    assert result.error.error_code == ERROR_WORKER_SCHEMA_INVALID
    assert WorkerJobRepository(db_session).get_job(payload.worker_job_id).status == "error"
    assert "worker.error" in visible_event_types(db_session, task.task_id)


def test_real_mcp_timeout_is_normalized(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    fake_client = FakeRealMcpClient(error=PlcMcpTimeoutError("timed out"))
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = real_adapter(db_session, tmp_path, fake_client).call_worker(payload)

    assert result.execution_status == "timeout"
    assert result.error is not None
    assert result.error.error_code == ERROR_MCP_TIMEOUT
    assert WorkerJobRepository(db_session).get_job(payload.worker_job_id).status == "timeout"
    assert "worker.timeout" in visible_event_types(db_session, task.task_id)


def test_real_mcp_connection_failure_is_normalized(
    db_session: Session,
    tmp_path: Path,
    task: TaskState,
) -> None:
    fake_client = FakeRealMcpClient(
        error=PlcMcpConnectionError(
            "connection failed",
            details={"exception_type": "ConnectError"},
        )
    )
    payload = worker_input(task, WorkerType.PLC_DEV, [raw_ref()])

    result = real_adapter(db_session, tmp_path, fake_client).call_worker(payload)

    assert result.execution_status == "error"
    assert result.error is not None
    assert result.error.error_code == ERROR_MCP_CONNECTION_ERROR
    assert WorkerJobRepository(db_session).get_job(payload.worker_job_id).status == "error"
    assert "worker.error" in visible_event_types(db_session, task.task_id)


class FakeRealMcpClient:
    def __init__(
        self,
        draft: LlmWorkerDraftOutput | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[tuple[str, McpWorkerRequest]] = []

    def call_worker_tool(
        self,
        tool_name: str,
        request: McpWorkerRequest,
    ) -> LlmWorkerDraftOutput:
        self.calls.append((tool_name, request))
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return self.draft


def real_adapter(
    db_session: Session,
    tmp_path: Path,
    fake_client: FakeRealMcpClient,
    *,
    mcp_mode: str = "real",
    plc_dev_mode: str | None = None,
    plc_test_mode: str | None = None,
    plc_worker_artifact_max_chars: int | None = None,
) -> McpAdapter:
    return McpAdapter(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        mcp_mode=mcp_mode,
        mcp_client=fake_client,  # type: ignore[arg-type]
        plc_dev_mode=plc_dev_mode,
        plc_test_mode=plc_test_mode,
        plc_worker_artifact_max_chars=plc_worker_artifact_max_chars,
    )


def real_dev_draft() -> LlmWorkerDraftOutput:
    return LlmWorkerDraftOutput.model_validate(
        {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Real MCP dev draft.",
            "artifact_writes": [
                {
                    "artifact_type": "requirements_ir",
                    "version": 1,
                    "name": "requirements_ir_v1.json",
                    "content": {"requirements": []},
                    "summary": "Requirements IR.",
                    "mime_type": "application/json",
                },
                {
                    "artifact_type": "plc_code",
                    "version": 1,
                    "name": "plc_code_v1.st",
                    "content": "PROGRAM Main\nEND_PROGRAM",
                    "summary": "PLC code.",
                    "mime_type": "text/plain",
                },
                {
                    "artifact_type": "io_contract",
                    "version": 1,
                    "name": "io_contract_v1.json",
                    "content": {"inputs": [], "outputs": []},
                    "summary": "IO contract.",
                    "mime_type": "application/json",
                },
            ],
            "next_recommended_action": "test",
            "metadata": {"worker_simulation": "test"},
        }
    )
