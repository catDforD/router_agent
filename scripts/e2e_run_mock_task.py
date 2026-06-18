"""Run one deterministic Router mock E2E scenario locally."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.agents.main_agent import build_main_agent_event, episode_output_from_task  # noqa: E402
from app.agents.output_schema import (  # noqa: E402
    IntakeClassificationOutput,
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentPlanStep,
)
from app.agents.tools import AgentToolContext, AgentToolResult, AgentToolService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.mcp.mock_worker import (  # noqa: E402
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
from app.models.db_models import ArtifactRow, GateResultRow, WorkerJobRow  # noqa: E402
from app.models.router_schema import EventType  # noqa: E402
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.event_service import EventService  # noqa: E402
from app.services.runtime_service import RuntimeService  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402


SCENARIO_NEED_CLARIFICATION = "need_clarification"
SUPPORTED_SCENARIOS = (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
    SCENARIO_NEED_CLARIFICATION,
)
TERMINAL_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}


class ScriptedE2ERunner:
    def __init__(
        self,
        *,
        classification: IntakeClassificationOutput,
        sequence: list[str],
        final_task_status: str | None,
        expected_rejections: dict[str, str] | None = None,
    ) -> None:
        self.classification = classification
        self.sequence = sequence
        self.final_task_status = final_task_status
        self.expected_rejections = expected_rejections or {}
        self.tool_results: list[AgentToolResult] = []

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        return self.classification

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        task_id = run_config.group_id
        tools = AgentToolService(context)
        for action in self.sequence:
            if action == "finalizing":
                self._emit_finalizing(context, task_id)
                continue
            result = _run_tool(tools, task_id, action)
            expected_rejection = self.expected_rejections.get(action)
            if expected_rejection is not None and result.violation is not None:
                if result.violation.code != expected_rejection:
                    raise RuntimeError(
                        f"expected {expected_rejection}, got {result.violation.code}"
                    )
            elif result.status != "applied":
                raise RuntimeError(result.model_dump_json(indent=2))
            self.tool_results.append(result)

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Local deterministic Router mock E2E scenario completed.",
            decisions=[
                MainAgentDecision(
                    decision_type="scripted_e2e_sequence",
                    summary="Ran deterministic Router E2E tool sequence.",
                    action="finish",
                    artifact_refs=_output_artifact_refs(self.tool_results),
                    details={
                        "tools": [result.tool for result in self.tool_results],
                        "statuses": [str(result.status) for result in self.tool_results],
                    },
                )
            ],
            artifact_refs=_output_artifact_refs(self.tool_results),
        )
        if self.final_task_status is not None:
            output = output.model_copy(
                update={
                    "final_task_status": self.final_task_status,
                    "phase": (
                        "completed"
                        if self.final_task_status in TERMINAL_STATUSES
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
                        status=str(result.status),
                        tool_name=result.tool,
                        worker_type=result.worker_type,
                    )
                    for index, result in enumerate(self.tool_results, start=1)
                ],
                "metadata": {
                    "tool_count": len(self.tool_results),
                    "tool_names": [result.tool for result in self.tool_results],
                },
            }
        )

    def _emit_finalizing(self, context: AgentToolContext, task_id: str) -> None:
        task = TaskRepository(context.session).get_task(task_id)
        EventService(context.session).append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message="Local E2E script is running Quality Gate before finish.",
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id},
                created_at=utc_now(),
            )
        )
        if context.checkpoint is not None:
            context.checkpoint()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one deterministic Router mock E2E scenario.",
    )
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_SCENARIOS,
        default=SCENARIO_DEV_TEST_PASS,
        help="Mock E2E scenario to run.",
    )
    parser.add_argument(
        "--message",
        default="Create conveyor motor logic with emergency stop validation.",
        help="Task message for the generated task.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    runner = runner_for_scenario(args.scenario)
    mock_scenario = (
        SCENARIO_DEV_TEST_PASS
        if args.scenario == SCENARIO_NEED_CLARIFICATION
        else args.scenario
    )

    with session_scope() as session:
        created = TaskService(
            session=session,
            artifact_root=settings.artifact_root,
        ).create_task(
            message=args.message,
            project_context={
                "target_plc_language": "ST",
                "target_platform": "Codesys",
            },
        )
        task_id = created.task.task_id

    result = RuntimeService(
        settings=settings,
        mock_scenario=mock_scenario,
        runner=runner,
    ).start_task(task_id)

    print_summary(task_id, result.status)


def runner_for_scenario(scenario: str) -> ScriptedE2ERunner:
    if scenario == SCENARIO_NEED_CLARIFICATION:
        return ScriptedE2ERunner(
            classification=clarification_classification(),
            sequence=[],
            final_task_status=None,
        )
    if scenario == SCENARIO_TEST_FAILED_THEN_REPAIR_PASS:
        return ScriptedE2ERunner(
            classification=classification(),
            sequence=["dev", "test", "repair", "test", "finalizing", "gate"],
            final_task_status="succeeded",
        )
    if scenario == SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS:
        return ScriptedE2ERunner(
            classification=classification(
                difficulty_level="L3",
                difficulty_reasons=["Safety constraints require formal verification."],
                difficulty_signals=signals(has_safety_constraints=True),
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
    if scenario == SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED:
        return ScriptedE2ERunner(
            classification=classification(requires_repair_loop=True),
            sequence=[
                "dev",
                "test",
                "repair",
                "test",
                "repair",
                "test",
                "repair",
                "test",
                "repair_limit_rejected",
                "finalizing",
                "gate",
            ],
            final_task_status="partial_failed",
            expected_rejections={"repair_limit_rejected": "repair_limit_reached"},
        )
    return ScriptedE2ERunner(
        classification=classification(),
        sequence=["dev", "test", "finalizing", "gate"],
        final_task_status="succeeded",
    )


def print_summary(task_id: str, runtime_status: str) -> None:
    with session_scope() as session:
        task = TaskRepository(session).get_task(task_id)
        worker_jobs = list(
            session.execute(
                select(WorkerJobRow)
                .where(WorkerJobRow.task_id == task_id)
                .order_by(WorkerJobRow.created_at, WorkerJobRow.id)
            ).scalars()
        )
        artifacts = list(
            session.execute(
                select(ArtifactRow)
                .where(ArtifactRow.task_id == task_id)
                .order_by(ArtifactRow.created_at, ArtifactRow.version, ArtifactRow.id)
            ).scalars()
        )
        events = EventService(session).list_visible_events(task_id)
        gate_results = list(
            session.execute(
                select(GateResultRow)
                .where(GateResultRow.task_id == task_id)
                .order_by(GateResultRow.created_at, GateResultRow.gate_type)
            ).scalars()
        )

    artifact_counts: dict[str, int] = {}
    for artifact in artifacts:
        artifact_counts[artifact.type] = artifact_counts.get(artifact.type, 0) + 1

    print(f"task_id: {task_id}")
    print(f"runtime_status: {runtime_status}")
    print(f"task_status: {task.status}")
    print(f"task_phase: {task.phase}")
    print(f"repair_rounds: {task.runtime_limits.repair_rounds}/{task.runtime_limits.max_repair_rounds}")
    print("worker_jobs:")
    if not worker_jobs:
        print("  - none")
    for job in worker_jobs:
        print(f"  - {job.worker_type}: {job.status} ({job.id})")
    print("artifacts:")
    if not artifact_counts:
        print("  - none")
    for artifact_type, count in sorted(artifact_counts.items()):
        print(f"  - {artifact_type}: {count}")
    print("events:")
    for event in events:
        print(f"  - {event.seq}: {event.type}")
    print("gate_results:")
    if not gate_results:
        print("  - none")
    for gate in gate_results:
        print(f"  - {gate.gate_type}: {gate.status} blocking={gate.blocking}")
    print()
    print(f"curl http://localhost:8000/api/tasks/{task_id}")
    print(f"curl http://localhost:8000/api/tasks/{task_id}/artifacts")
    print(f"curl -N http://localhost:8000/api/tasks/{task_id}/events")


def classification(**updates: Any) -> IntakeClassificationOutput:
    values: dict[str, Any] = {
        "normalized_goal": "Create motor control PLC logic with validation.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.55,
        "difficulty_confidence": 0.86,
        "difficulty_reasons": ["Development with validation."],
        "difficulty_signals": signals(has_io_points=True),
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }
    values.update(updates)
    return IntakeClassificationOutput.model_validate(values)


def clarification_classification() -> IntakeClassificationOutput:
    values = classification(
        difficulty_level="L1",
        difficulty_reasons=["Platform and I/O details are missing."],
        requires_test=False,
        requires_formal=False,
        need_clarification=True,
        clarification_questions=[
            {
                "question": "Which PLC platform and I/O names should be used?",
                "reason": "The worker needs concrete target details.",
                "required": True,
            }
        ],
    ).model_dump(mode="json")
    values["difficulty_signals"]["requirement_incomplete"] = True
    return IntakeClassificationOutput.model_validate(values)


def signals(**updates: bool) -> dict[str, bool]:
    values = {
        "has_existing_code": False,
        "has_io_points": False,
        "has_timing_logic": False,
        "has_state_machine": False,
        "has_safety_constraints": False,
        "has_emergency_stop": False,
        "has_interlock": False,
        "has_fault_latching": False,
        "has_mode_switching": False,
        "multi_module": False,
        "requirement_incomplete": False,
    }
    values.update(updates)
    return values


def _run_tool(
    tools: AgentToolService,
    task_id: str,
    action: str,
) -> AgentToolResult:
    if action == "dev":
        return tools.call_plc_dev(task_id)
    if action == "test":
        return tools.call_plc_test(task_id)
    if action == "formal":
        return tools.call_plc_formal(task_id)
    if action in {"repair", "repair_limit_rejected"}:
        return tools.call_plc_repair(task_id)
    if action == "gate":
        return tools.run_quality_gate(task_id)
    raise ValueError(f"unsupported action: {action}")


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


if __name__ == "__main__":
    main()
