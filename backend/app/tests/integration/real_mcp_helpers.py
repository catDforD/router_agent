from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.tools import AgentToolContext, AgentToolService
from app.mcp.adapter import McpAdapter
from app.mcp.draft import LlmWorkerDraftOutput, McpWorkerRequest
from app.models.db_models import Base
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ExpectedOutputSpec,
    FailureSource,
    FailureStatus,
    Severity,
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
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.workers.worker_result_handler import handle_worker_result


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def task(db_session: Session) -> TaskState:
    task_state = TaskState.model_validate(
        json.loads((FIXTURE_DIR / "task_state.valid.json").read_text(encoding="utf-8"))
    )
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


class FakeMcpClient:
    def __init__(
        self,
        drafts: dict[str, LlmWorkerDraftOutput] | None = None,
        *,
        tools: list[str] | None = None,
    ) -> None:
        self.drafts = drafts or {}
        self.tools = tools or [
            "plc_dev.run",
            "plc_test.run",
            "plc_formal.run",
            "plc_repair.run",
        ]
        self.calls: list[tuple[str, McpWorkerRequest]] = []

    def list_tools(self) -> list[str]:
        return self.tools

    def call_worker_tool(
        self,
        tool_name: str,
        request: McpWorkerRequest,
    ) -> LlmWorkerDraftOutput:
        self.calls.append((tool_name, request))
        return self.drafts[tool_name]


def real_adapter(
    session: Session,
    tmp_path: Path,
    fake_client: FakeMcpClient,
    *,
    mcp_mode: str = "real",
    plc_dev_mode: str | None = None,
    plc_test_mode: str | None = None,
    plc_formal_mode: str | None = None,
    plc_repair_mode: str | None = None,
) -> McpAdapter:
    return McpAdapter(
        session=session,
        artifact_root=tmp_path / "artifacts",
        mcp_mode=mcp_mode,
        mcp_client=fake_client,  # type: ignore[arg-type]
        plc_dev_mode=plc_dev_mode,
        plc_test_mode=plc_test_mode,
        plc_formal_mode=plc_formal_mode,
        plc_repair_mode=plc_repair_mode,
    )


def dispatch_and_apply(
    adapter: McpAdapter,
    session: Session,
    worker_input: WorkerInput,
) -> TaskState:
    result = adapter.call_worker(worker_input)
    return handle_worker_result(result, session=session).task


def write_artifact(
    session: Session,
    tmp_path: Path,
    task_id: str,
    artifact_type: ArtifactType,
    content: Any,
    *,
    version: int = 1,
    name: str | None = None,
) -> ArtifactRef:
    store = ArtifactStore(session, tmp_path / "artifacts")
    artifact = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task_id,
            artifact_type=artifact_type,
            version=version,
            name=name or f"{artifact_type.value}_v{version}.txt",
            content=content,
            summary=f"{artifact_type.value} artifact.",
            mime_type="application/json" if isinstance(content, dict) else "text/plain",
        )
    ).artifact
    return store.get_artifact_ref(artifact.artifact_id)


def worker_input(
    task: TaskState,
    worker_type: WorkerType,
    input_artifacts: list[ArtifactRef],
) -> WorkerInput:
    worker = worker_type.value
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=f"worker-job-{worker}-{len(input_artifacts)}",
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode={
            WorkerType.PLC_DEV: WorkerMode.CREATE,
            WorkerType.PLC_TEST: WorkerMode.TEST,
            WorkerType.PLC_FORMAL: WorkerMode.FORMAL_VERIFY,
            WorkerType.PLC_REPAIR: WorkerMode.REPAIR,
        }[worker_type],
        objective=f"Run {worker}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.normalized_goal or task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=task.runtime_limits.repair_rounds,
            assumptions=[],
            selected_failure_ids=[
                failure.failure_id
                for failure in task.failures
                if failure.status == FailureStatus.OPEN.value
            ],
        ),
        constraints=[],
        expected_outputs=[
            ExpectedOutputSpec(
                artifact_type=artifact_type,
                required=True,
                description=f"Expected {artifact_type.value}.",
            )
            for artifact_type in {
                WorkerType.PLC_DEV: [ArtifactType.PLC_CODE, ArtifactType.IO_CONTRACT],
                WorkerType.PLC_TEST: [ArtifactType.TEST_REPORT],
                WorkerType.PLC_FORMAL: [ArtifactType.FORMAL_REPORT],
                WorkerType.PLC_REPAIR: [
                    ArtifactType.PATCH,
                    ArtifactType.PLC_CODE,
                    ArtifactType.REPAIR_SUMMARY,
                ],
            }[worker_type]
        ],
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=f"worker-job-{worker}-{len(input_artifacts)}"),
        idempotency_key=f"{task.task_id}:worker-job-{worker}-{len(input_artifacts)}",
        created_at=task.created_at,
    )


def draft_dev() -> LlmWorkerDraftOutput:
    return draft(
        "passed",
        "LLM-backed dev generated PLC code.",
        [
            artifact_write("requirements_ir", "requirements_ir_v1.json", {"requirements": []}),
            artifact_write("plc_code", "plc_code_v1.st", "PROGRAM Main\nEND_PROGRAM"),
            artifact_write("io_contract", "io_contract_v1.json", {"inputs": [], "outputs": []}),
        ],
        next_action="test",
    )


def draft_test_passed() -> LlmWorkerDraftOutput:
    return draft(
        "passed",
        "LLM-backed tests passed.",
        [artifact_write("test_report", "test_report.json", {"status": "passed"})],
        metrics={"test_metrics": {"total": 1, "passed": 1, "failed": 0}},
        next_action="run_quality_gate",
    )


def draft_test_failed() -> LlmWorkerDraftOutput:
    return draft(
        "failed",
        "LLM-backed tests found a blocking failure.",
        [
            artifact_write("test_report", "test_report.json", {"status": "failed"}),
            artifact_write("failing_trace", "failing_trace.json", {"case": "estop"}),
        ],
        blocking=True,
        failures=[failure("test", "Emergency stop test failed")],
        metrics={"test_metrics": {"total": 1, "passed": 0, "failed": 1}},
        next_action="repair",
    )


def draft_formal_passed() -> LlmWorkerDraftOutput:
    return draft(
        "passed",
        "LLM-backed formal checks passed.",
        [artifact_write("formal_report", "formal_report.json", {"status": "passed"})],
        metrics={"formal_metrics": {"total_properties": 1, "passed_properties": 1}},
        next_action="run_quality_gate",
    )


def draft_formal_failed() -> LlmWorkerDraftOutput:
    return draft(
        "failed",
        "LLM-backed formal checks found a counterexample.",
        [
            artifact_write("formal_report", "formal_report.json", {"status": "failed"}),
            artifact_write("counterexample", "counterexample.json", {"trace": []}),
        ],
        blocking=True,
        failures=[failure("formal", "Formal property failed")],
        metrics={"formal_metrics": {"total_properties": 1, "failed_properties": 1}},
        next_action="repair",
    )


def draft_repair(from_code_artifact_id: str | None = None) -> LlmWorkerDraftOutput:
    return draft(
        "passed",
        "LLM-backed repair produced a patch.",
        [
            artifact_write(
                "patch",
                "patch_v1.diff",
                "--- a\n+++ b\n",
                metadata={
                    "patch_metadata": {
                        "from_code_artifact_id": from_code_artifact_id,
                        "changed_files": 1,
                        "changed_lines": 2,
                        "repair_round": 1,
                    }
                },
            ),
            artifact_write("plc_code", "plc_code_v2.st", "PROGRAM Main\nEND_PROGRAM", version=2),
            artifact_write("repair_summary", "repair_summary.json", {"repair_round": 1}),
        ],
        metrics={"repair_metrics": {"changed_files": 1, "changed_lines": 2}},
        next_action="test",
    )


def draft(
    status: str,
    summary: str,
    artifact_writes: list[dict[str, Any]],
    *,
    blocking: bool = False,
    failures: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    next_action: str = "none",
) -> LlmWorkerDraftOutput:
    return LlmWorkerDraftOutput.model_validate(
        {
            "outcome": {
                "status": status,
                "blocking": blocking,
                "confidence": 0.9,
            },
            "summary": summary,
            "artifact_writes": artifact_writes,
            "failures": failures or [],
            "metrics": metrics or {},
            "next_recommended_action": next_action,
            "metadata": {"worker_simulation": "test"},
        }
    )


def artifact_write(
    artifact_type: str,
    name: str,
    content: Any,
    *,
    version: int = 1,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "version": version,
        "name": name,
        "content": content,
        "summary": f"{artifact_type} artifact.",
        "metadata": metadata,
        "mime_type": "application/json" if isinstance(content, dict) else "text/plain",
    }


def failure(source: str, title: str) -> dict[str, Any]:
    return {
        "failure_id": f"failure-{source}",
        "source": FailureSource.TEST.value if source == "test" else FailureSource.FORMAL.value,
        "severity": Severity.BLOCKING.value,
        "title": title,
        "description": title,
        "expected": "Safe output.",
        "actual": "Unsafe output.",
        "evidence_artifact_ids": [],
        "status": FailureStatus.OPEN.value,
        "created_at": datetime.now(UTC).isoformat(),
    }


def agent_tool_service(
    session: Session,
    tmp_path: Path,
) -> AgentToolService:
    return AgentToolService(
        AgentToolContext(
            session=session,
            artifact_root=tmp_path / "artifacts",
            mcp_mode="real",
        )
    )
