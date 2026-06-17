"""Call one mock MCP worker through the Router adapter for local inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.ids import new_session_id, new_task_id  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.mcp.adapter import McpAdapter  # noqa: E402
from app.models.router_schema import (  # noqa: E402
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    CurrentArtifacts,
    ExpectedOutputSpec,
    TaskPhase,
    TaskState,
    TaskStatus,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call a deterministic mock PLC worker through the Router adapter.",
    )
    parser.add_argument(
        "--worker",
        required=True,
        choices=[worker.value for worker in WorkerType],
        help="Worker type to invoke.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Mock scenario override. Defaults to MOCK_SCENARIO.",
    )
    return parser.parse_args()


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def create_classified_task(task_repository: TaskRepository) -> TaskState:
    now = utc_now()
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    task = base.model_copy(
        deep=True,
        update={
            "task_id": new_task_id(),
            "session_id": new_session_id(),
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "normalized_goal": base.raw_user_request,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "event_seq": 0,
            "current_artifacts": CurrentArtifacts(all_artifact_ids=[]),
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "failures": [],
        },
    )
    task_repository.create_task(task)
    return task


def create_prerequisite_artifacts(
    *,
    store: ArtifactStore,
    task: TaskState,
    worker_type: str,
) -> list[ArtifactRef]:
    creator = ArtifactCreator(type=ArtifactCreatorType.RUNTIME)
    raw = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.RAW_USER_REQUEST,
            version=1,
            name="raw_user_request.json",
            content={"message": task.raw_user_request},
            summary="Raw request for mock worker call.",
            created_by=creator,
            mime_type="application/json",
        )
    ).artifact
    raw_ref = store.get_artifact_ref(raw.artifact_id)
    if worker_type == WorkerType.PLC_DEV.value:
        return [raw_ref]

    requirements = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.REQUIREMENTS_IR,
            version=1,
            name="requirements_ir_v1.json",
            content={"goal": task.raw_user_request, "requirements": ["mock precondition"]},
            summary="Prerequisite requirements for mock worker call.",
            created_by=creator,
            parent_artifact_ids=(raw_ref.artifact_id,),
            mime_type="application/json",
        )
    ).artifact
    code = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.PLC_CODE,
            version=1,
            name="plc_code_v1.st",
            content="FUNCTION_BLOCK FB_MotorControl\nMotorRun := StartCmd;\nEND_FUNCTION_BLOCK\n",
            summary="Prerequisite PLC code for mock worker call.",
            created_by=creator,
            parent_artifact_ids=(raw_ref.artifact_id,),
            metadata={"code_metadata": {"code_version": 1, "is_current": True}},
            mime_type="text/plain",
        )
    ).artifact
    refs = [
        store.get_artifact_ref(requirements.artifact_id),
        store.get_artifact_ref(code.artifact_id),
    ]
    if worker_type != WorkerType.PLC_REPAIR.value:
        return refs

    test_report = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.TEST_REPORT,
            version=1,
            name="test_report_failed.json",
            content={"status": "failed", "failed": 1},
            summary="Prerequisite failing test report for repair.",
            created_by=creator,
            parent_artifact_ids=(code.artifact_id,),
            metadata={"test_metadata": {"status": "failed", "total": 1, "failed": 1}},
            mime_type="application/json",
        )
    ).artifact
    refs.append(store.get_artifact_ref(test_report.artifact_id))
    return refs


def build_worker_input(
    *,
    task: TaskState,
    worker_type: str,
    input_artifacts: list[ArtifactRef],
) -> WorkerInput:
    now = utc_now()
    worker_job_id = f"worker-job-{worker_type.replace('-', '-')}-{uuid4().hex[:12]}"
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=worker_job_id,
        worker_type=worker_type,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker_type],
        mode=_worker_mode(worker_type),
        objective=f"Run mock {worker_type}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.normalized_goal or task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language=(
                str(task.project_context.target_plc_language)
                if task.project_context.target_plc_language is not None
                else None
            ),
            target_platform=task.project_context.target_platform,
            repair_round=task.runtime_limits.repair_rounds,
            assumptions=task.assumptions,
        ),
        constraints=[],
        expected_outputs=_expected_outputs(worker_type),
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=worker_job_id),
        idempotency_key=f"{task.task_id}:{worker_job_id}",
        created_at=now,
        metadata={"source": "dev_call_mock_worker"},
    )


def _worker_mode(worker_type: str) -> WorkerMode:
    return {
        WorkerType.PLC_DEV.value: WorkerMode.CREATE,
        WorkerType.PLC_TEST.value: WorkerMode.TEST,
        WorkerType.PLC_FORMAL.value: WorkerMode.FORMAL_VERIFY,
        WorkerType.PLC_REPAIR.value: WorkerMode.REPAIR,
    }[worker_type]


def _expected_outputs(worker_type: str) -> list[ExpectedOutputSpec]:
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


def main() -> None:
    args = parse_args()
    settings = get_settings()
    scenario = args.scenario or settings.mock_scenario
    with session_scope() as session:
        task = create_classified_task(TaskRepository(session))
        store = ArtifactStore(session=session, artifact_root=settings.artifact_root)
        input_artifacts = create_prerequisite_artifacts(
            store=store,
            task=task,
            worker_type=args.worker,
        )
        worker_input = build_worker_input(
            task=task,
            worker_type=args.worker,
            input_artifacts=input_artifacts,
        )
        result = McpAdapter(
            session=session,
            artifact_root=settings.artifact_root,
            mcp_mode=settings.mcp_mode,
            mock_scenario=scenario,
        ).call_worker(worker_input)

    print(f"task_id: {task.task_id}")
    print(f"worker_job_id: {result.worker_job_id}")
    print(f"execution_status: {result.execution_status}")
    print(f"outcome_status: {result.outcome.status}")
    print(f"summary: {result.summary}")
    print("produced_artifact_ids:")
    for artifact in result.produced_artifacts:
        print(f"  - {artifact.artifact_id} ({artifact.type}:v{artifact.version})")
    print()
    print("Example checks:")
    print(f"curl http://localhost:8000/api/tasks/{task.task_id}/artifacts")
    print(f"curl 'http://localhost:8000/api/tasks/{task.task_id}/events?include_internal=true'")


if __name__ == "__main__":
    main()
