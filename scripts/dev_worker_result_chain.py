"""Run mock worker results through the WorkerResult Handler for local inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
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
from app.mcp.mock_worker import (  # noqa: E402
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
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
from app.workers.worker_result_handler import handle_worker_result  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"
SUPPORTED_SCENARIOS = (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a mock worker chain and apply WorkerResult state updates.",
    )
    parser.add_argument(
        "--scenario",
        default=SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
        choices=SUPPORTED_SCENARIOS,
        help="Mock scenario chain to execute.",
    )
    return parser.parse_args()


def load_fixture(name: str) -> dict[str, Any]:
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
            "unresolved_questions": [],
        },
    )
    return task_repository.create_task(task)


def create_raw_artifact(store: ArtifactStore, task: TaskState) -> ArtifactRef:
    artifact = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=ArtifactType.RAW_USER_REQUEST,
            version=1,
            name="raw_user_request.json",
            content={"message": task.raw_user_request},
            summary="Raw request for worker result chain.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="application/json",
        )
    ).artifact
    return store.get_artifact_ref(artifact.artifact_id)


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
        objective=f"Run mock {worker_type} in worker result chain.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.normalized_goal or task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language=_optional_value(task.project_context.target_plc_language),
            target_platform=task.project_context.target_platform,
            repair_round=task.runtime_limits.repair_rounds,
            selected_failure_ids=[
                failure.failure_id
                for failure in task.failures
                if failure.status == "open"
            ],
            assumptions=task.assumptions,
        ),
        constraints=[],
        expected_outputs=_expected_outputs(worker_type),
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=worker_job_id),
        idempotency_key=f"{task.task_id}:{worker_job_id}",
        created_at=now,
        metadata={"source": "dev_worker_result_chain"},
    )


def run_chain(scenario: str) -> TaskState:
    settings = get_settings()
    with session_scope() as session:
        task_repository = TaskRepository(session)
        artifact_store = ArtifactStore(
            session=session,
            artifact_root=settings.artifact_root,
        )
        adapter = McpAdapter(
            session=session,
            artifact_root=settings.artifact_root,
            mcp_mode=settings.mcp_mode,
            mock_scenario=scenario,
        )
        task = create_classified_task(task_repository)
        raw = create_raw_artifact(artifact_store, task)

        dev_input = build_worker_input(
            task=task_repository.get_task(task.task_id),
            worker_type=WorkerType.PLC_DEV.value,
            input_artifacts=[raw],
        )
        dev_result = adapter.call_worker(dev_input, scenario=scenario)
        task = handle_worker_result(dev_result, session=session).task
        print_result(dev_result, task)

        if scenario == SCENARIO_DEV_TEST_PASS:
            test_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_TEST.value,
                    input_artifacts=_test_or_formal_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(test_result, session=session).task
            print_result(test_result, task)
        elif scenario == SCENARIO_TEST_FAILED_THEN_REPAIR_PASS:
            test_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_TEST.value,
                    input_artifacts=_test_or_formal_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(test_result, session=session).task
            print_result(test_result, task)

            repair_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_REPAIR.value,
                    input_artifacts=_repair_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(repair_result, session=session).task
            print_result(repair_result, task)

            regression_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_TEST.value,
                    input_artifacts=_test_or_formal_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(regression_result, session=session).task
            print_result(regression_result, task)
        elif scenario == SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS:
            formal_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_FORMAL.value,
                    input_artifacts=_test_or_formal_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(formal_result, session=session).task
            print_result(formal_result, task)

            repair_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_REPAIR.value,
                    input_artifacts=_repair_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(repair_result, session=session).task
            print_result(repair_result, task)

            formal_regression_result = adapter.call_worker(
                build_worker_input(
                    task=task,
                    worker_type=WorkerType.PLC_FORMAL.value,
                    input_artifacts=_test_or_formal_inputs(task),
                ),
                scenario=scenario,
            )
            task = handle_worker_result(formal_regression_result, session=session).task
            print_result(formal_regression_result, task)

        print_state_summary(task)
        return task


def print_result(result: Any, task: TaskState) -> None:
    print(
        "handled worker: "
        f"{result.worker_type} job={result.worker_job_id} "
        f"execution={result.execution_status} outcome={result.outcome.status} "
        f"repair_rounds={task.runtime_limits.repair_rounds}"
    )


def print_state_summary(task: TaskState) -> None:
    print()
    print(f"task_id: {task.task_id}")
    print(f"status: {task.status}")
    print(f"phase: {task.phase}")
    print(f"completed_worker_job_ids: {', '.join(task.completed_worker_job_ids)}")
    print(f"repair_rounds: {task.runtime_limits.repair_rounds}/{task.runtime_limits.max_repair_rounds}")
    print("current_artifacts:")
    for label, ref in _artifact_pointers(task):
        if ref is not None:
            print(f"  - {label}: {ref.artifact_id} ({ref.type}:v{ref.version})")
    print("gates:")
    print(f"  latest_test_passed: {task.gates.latest_test_passed}")
    print(f"  latest_formal_passed: {task.gates.latest_formal_passed}")
    print(f"  regression_required: {task.gates.regression_required}")
    print(f"  formal_regression_required: {task.gates.formal_regression_required}")
    print(f"  has_blocking_failure: {task.gates.has_blocking_failure}")
    print("open_failures:")
    open_failures = [failure for failure in task.failures if failure.status == "open"]
    if not open_failures:
        print("  - none")
    for failure in open_failures:
        print(f"  - {failure.failure_id}: {failure.source} {failure.title}")
    print()
    print("Example checks:")
    print(f"curl http://localhost:8000/api/tasks/{task.task_id}/artifacts")
    print(f"curl 'http://localhost:8000/api/tasks/{task.task_id}/events?include_internal=true'")


def _test_or_formal_inputs(task: TaskState) -> list[ArtifactRef]:
    if task.current_artifacts.requirements_ir is None:
        raise RuntimeError("task has no requirements_ir artifact")
    if task.current_artifacts.current_code is None:
        raise RuntimeError("task has no current_code artifact")
    return [
        task.current_artifacts.requirements_ir,
        task.current_artifacts.current_code,
    ]


def _repair_inputs(task: TaskState) -> list[ArtifactRef]:
    if task.current_artifacts.current_code is None:
        raise RuntimeError("task has no current_code artifact")
    evidence = [
        ref
        for ref in (
            task.current_artifacts.latest_test_report,
            task.current_artifacts.latest_failing_trace,
            task.current_artifacts.latest_formal_report,
            task.current_artifacts.latest_counterexample,
        )
        if ref is not None
    ]
    if not evidence:
        raise RuntimeError("task has no repair evidence artifact")
    return [task.current_artifacts.current_code, *evidence]


def _artifact_pointers(task: TaskState) -> list[tuple[str, ArtifactRef | None]]:
    artifacts = task.current_artifacts
    return [
        ("requirements_ir", artifacts.requirements_ir),
        ("current_code", artifacts.current_code),
        ("current_io_contract", artifacts.current_io_contract),
        ("latest_test_report", artifacts.latest_test_report),
        ("latest_failing_trace", artifacts.latest_failing_trace),
        ("latest_formal_report", artifacts.latest_formal_report),
        ("latest_counterexample", artifacts.latest_counterexample),
        ("latest_patch", artifacts.latest_patch),
        ("latest_repair_summary", artifacts.latest_repair_summary),
    ]


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


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def main() -> None:
    args = parse_args()
    run_chain(args.scenario)


if __name__ == "__main__":
    main()
