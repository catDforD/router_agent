"""Invoke Main Agent runtime tools directly for local inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.ids import new_session_id, new_task_id  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.mcp.mock_worker import SCENARIO_TEST_FAILED_THEN_REPAIR_PASS  # noqa: E402
from app.models.router_schema import (  # noqa: E402
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
    ArtifactType,
    CurrentArtifacts,
    DifficultyProfile,
    DifficultySignals,
    GateState,
    TaskPhase,
    TaskState,
    TaskStatus,
)
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"
SUPPORTED_TOOLS = (
    "call_plc_dev",
    "call_plc_test",
    "call_plc_formal",
    "call_plc_repair",
    "run_quality_gate",
    "write_final_report",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke one Main Agent runtime tool without running Main Agent.",
    )
    parser.add_argument(
        "--tool",
        required=True,
        choices=SUPPORTED_TOOLS,
        help="Tool to invoke.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Mock scenario override. Defaults to MOCK_SCENARIO.",
    )
    return parser.parse_args()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def create_classified_task(
    task_repository: TaskRepository,
    *,
    qa: bool = False,
) -> TaskState:
    now = utc_now()
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    task = base.model_copy(
        deep=True,
        update={
            "task_id": new_task_id(),
            "session_id": new_session_id(),
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "task_type": "qa" if qa else "new_plc_development",
            "difficulty": _quiet_difficulty() if qa else base.difficulty,
            "gates": _quiet_gates() if qa else base.gates,
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
            summary="Raw request for agent tool script.",
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="application/json",
        )
    ).artifact
    return store.get_artifact_ref(artifact.artifact_id)


def invoke_tool(service: AgentToolService, task: TaskState, tool: str) -> AgentToolResult:
    if tool == "call_plc_dev":
        return service.call_plc_dev(task.task_id)
    if tool == "call_plc_test":
        service.call_plc_dev(task.task_id)
        return service.call_plc_test(task.task_id)
    if tool == "call_plc_formal":
        service.call_plc_dev(task.task_id)
        return service.call_plc_formal(task.task_id)
    if tool == "call_plc_repair":
        service.call_plc_dev(task.task_id)
        service.call_plc_test(task.task_id)
        return service.call_plc_repair(task.task_id)
    if tool == "run_quality_gate":
        return service.run_quality_gate(task.task_id)
    if tool == "write_final_report":
        service.run_quality_gate(task.task_id)
        return service.write_final_report(
            task.task_id,
            final_status="succeeded",
            summary="Final report written from dev_call_agent_tool.py.",
        )
    raise ValueError(f"unsupported tool: {tool}")


def print_result(result: AgentToolResult, task: TaskState) -> None:
    print(f"task_id: {task.task_id}")
    print(f"tool: {result.tool}")
    print(f"status: {result.status}")
    print(f"summary: {result.summary}")
    if result.violation is not None:
        print(f"violation: {result.violation.code} - {result.violation.message}")
    if result.error is not None:
        print(f"error: {result.error.error_code} - {result.error.message}")
    print("artifact_refs:")
    if not result.artifact_refs:
        print("  - none")
    for artifact in result.artifact_refs:
        print(f"  - {artifact.artifact_id} ({artifact.type}:v{artifact.version})")
    print("gate_state:")
    if result.gate_state is None:
        print("  - unavailable")
    else:
        print(f"  test_required: {result.gate_state.test_required}")
        print(f"  formal_required: {result.gate_state.formal_required}")
        print(f"  latest_test_passed: {result.gate_state.latest_test_passed}")
        print(f"  latest_formal_passed: {result.gate_state.latest_formal_passed}")
        print(f"  regression_required: {result.gate_state.regression_required}")
        print(
            "  formal_regression_required: "
            f"{result.gate_state.formal_regression_required}"
        )
        print(f"  has_blocking_failure: {result.gate_state.has_blocking_failure}")
        print(f"  can_finish_as_success: {result.gate_state.can_finish_as_success}")
    print("open_failures:")
    open_failures = [failure for failure in result.failures if failure.status == "open"]
    if not open_failures:
        print("  - none")
    for failure in open_failures:
        print(f"  - {failure.failure_id}: {failure.source} {failure.title}")
    print()
    print("Example checks:")
    print(f"curl http://localhost:8000/api/tasks/{task.task_id}/artifacts")
    print(f"curl 'http://localhost:8000/api/tasks/{task.task_id}/events?include_internal=true'")


def _quiet_gates() -> GateState:
    return GateState(
        test_required=False,
        formal_required=False,
        regression_required=False,
        formal_regression_required=False,
        latest_test_passed=None,
        latest_formal_passed=None,
        has_blocking_failure=False,
        can_finish_as_success=False,
    )


def _quiet_difficulty() -> DifficultyProfile:
    return DifficultyProfile(
        level="L1",
        score=0.1,
        confidence=0.9,
        reasons=["QA task for agent tool script."],
        signals=DifficultySignals(
            has_existing_code=False,
            has_io_points=False,
            has_timing_logic=False,
            has_state_machine=False,
            has_safety_constraints=False,
            has_emergency_stop=False,
            has_interlock=False,
            has_fault_latching=False,
            has_mode_switching=False,
            multi_module=False,
            requirement_incomplete=False,
        ),
        requires_test=False,
        requires_formal=False,
        requires_repair_loop=False,
        need_clarification=False,
    )


def main() -> None:
    args = parse_args()
    settings = get_settings()
    scenario = args.scenario or settings.mock_scenario
    if args.tool == "call_plc_repair" and args.scenario is None:
        scenario = SCENARIO_TEST_FAILED_THEN_REPAIR_PASS

    with session_scope() as session:
        task_repository = TaskRepository(session)
        artifact_store = ArtifactStore(
            session=session,
            artifact_root=settings.artifact_root,
        )
        task = create_classified_task(
            task_repository,
            qa=args.tool in {"run_quality_gate", "write_final_report"},
        )
        create_raw_artifact(artifact_store, task)
        service = AgentToolService(
            AgentToolContext(
                session=session,
                artifact_root=settings.artifact_root,
                mcp_mode=settings.mcp_mode,
                mock_scenario=scenario,
            )
        )
        result = invoke_tool(service, task, args.tool)
        latest_task = task_repository.get_task(task.task_id)

    print_result(result, latest_task)


if __name__ == "__main__":
    main()
