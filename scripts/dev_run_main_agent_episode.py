"""Run one mock Main Agent episode locally without live OpenAI calls."""

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

from app.agents.main_agent import MainAgentService, episode_output_from_task  # noqa: E402
from app.agents.output_schema import (  # noqa: E402
    IntakeClassificationOutput,
    MainAgentPlanStep,
)
from app.agents.tools import AgentToolContext, AgentToolService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.mcp.mock_worker import (  # noqa: E402
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
)
from app.models.router_schema import TaskPhase, TaskStatus  # noqa: E402
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402


SUPPORTED_SCENARIOS = (
    SCENARIO_DEV_TEST_PASS,
    SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
    SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS,
)


class LocalMockMainAgentRunner:
    """Small deterministic runner for manual local smoke checks."""

    def __init__(self, *, scenario: str, clarification: bool = False) -> None:
        self.scenario = scenario
        self.clarification = clarification

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        return _clarification_classification() if self.clarification else _classification(
            formal=self.scenario == SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS
        )

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
        plan: list[MainAgentPlanStep] = []
        for index, action in enumerate(_sequence_for(self.scenario), start=1):
            result = _run_tool(tools, task_id, action)
            plan.append(
                MainAgentPlanStep(
                    order=index,
                    action=result.tool,
                    status=result.status,
                    tool_name=result.tool,
                )
            )
            if result.status == "no-op" and result.next_recommended_action == "return_final_output":
                continue
            if result.status != "applied":
                break

        task = TaskRepository(context.session).get_task(task_id)
        output = episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Local mock Main Agent episode finished.",
        )
        if task.gates.can_finish_as_success:
            output = output.model_copy(
                update={
                    "final_task_status": TaskStatus.SUCCEEDED,
                    "phase": TaskPhase.COMPLETED.value,
                    "summary": "Local mock Main Agent episode succeeded.",
                }
            )
        return output.model_copy(update={"plan": plan})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MainAgentService with mock workers and a fake runner.",
    )
    parser.add_argument(
        "--message",
        default="Create motor start/stop logic with validation.",
        help="Task message to classify and run.",
    )
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_SCENARIOS,
        default=SCENARIO_DEV_TEST_PASS,
        help="Mock worker scenario.",
    )
    parser.add_argument(
        "--clarification",
        action="store_true",
        help="Stop after intake with a required clarification question.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
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
        output = MainAgentService(
            session=session,
            artifact_root=settings.artifact_root,
            mcp_mode=settings.mcp_mode,
            mock_scenario=args.scenario,
            model=settings.main_agent_model,
            max_turns=settings.main_agent_max_turns,
            runner=LocalMockMainAgentRunner(
                scenario=args.scenario,
                clarification=args.clarification,
            ),
        ).run_episode(created.task.task_id)

    print(output.model_dump_json(indent=2))


def _classification(*, formal: bool) -> IntakeClassificationOutput:
    return IntakeClassificationOutput(
        normalized_goal="Create motor control PLC logic with validation.",
        task_type="new_plc_development",
        difficulty_level="L3" if formal else "L2",
        difficulty_score=0.65 if formal else 0.5,
        difficulty_confidence=0.85,
        difficulty_reasons=[
            (
                "Safety constraints require formal verification."
                if formal
                else "Development requires test validation."
            )
        ],
        difficulty_signals=_signals(has_safety_constraints=formal, has_io_points=True),
        requires_test=True,
        requires_formal=formal,
        requires_repair_loop=False,
        need_clarification=False,
        clarification_questions=[],
    )


def _clarification_classification() -> IntakeClassificationOutput:
    return IntakeClassificationOutput(
        normalized_goal="Create PLC logic after target details are confirmed.",
        task_type="new_plc_development",
        difficulty_level="L1",
        difficulty_score=0.2,
        difficulty_confidence=0.75,
        difficulty_reasons=["The request is missing required target details."],
        difficulty_signals=_signals(requirement_incomplete=True),
        requires_test=False,
        requires_formal=False,
        requires_repair_loop=False,
        need_clarification=True,
        clarification_questions=[
            {
                "question": "Which PLC platform and I/O names should be used?",
                "reason": "The worker needs concrete target details.",
                "required": True,
            }
        ],
    )


def _sequence_for(scenario: str) -> list[str]:
    if scenario == SCENARIO_TEST_FAILED_THEN_REPAIR_PASS:
        return ["dev", "test", "repair", "test", "gate", "finish"]
    if scenario == SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS:
        return ["dev", "test", "formal", "repair", "test", "formal", "gate", "finish"]
    return ["dev", "test", "gate", "finish"]


def _run_tool(tools: AgentToolService, task_id: str, action: str) -> Any:
    if action == "dev":
        return tools.call_plc_dev(task_id)
    if action == "test":
        return tools.call_plc_test(task_id)
    if action == "formal":
        return tools.call_plc_formal(task_id)
    if action == "repair":
        return tools.call_plc_repair(task_id)
    if action == "gate":
        return tools.run_quality_gate(task_id)
    if action == "finish":
        return tools.finish_task(task_id)
    raise ValueError(f"unsupported action: {action}")


def _signals(**updates: bool) -> dict[str, bool]:
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


if __name__ == "__main__":
    main()
