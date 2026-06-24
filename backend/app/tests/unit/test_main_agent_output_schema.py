import pytest
from pydantic import ValidationError

from app.agents.output_schema import (
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
    MainAgentPlanStep,
)


def test_episode_output_supports_success_shape() -> None:
    artifact = MainAgentArtifactReference(
        artifact_id="artifact-code-001",
        type="plc_code",
        version=1,
        summary="Generated PLC code.",
    )
    output = MainAgentEpisodeOutput(
        task_id="task-001",
        main_agent_run_id="main-agent-run-001",
        final_task_status="succeeded",
        phase="completed",
        decisions=[
            MainAgentDecision(
                decision_type="dispatch",
                summary="Ran PLC development and testing.",
                artifact_refs=[artifact],
            )
        ],
        plan=[
            MainAgentPlanStep(
                order=1,
                action="call_plc_dev",
                status="completed",
                tool_name="call_plc_dev",
            )
        ],
        artifact_refs=[artifact],
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

    assert output.final_task_status == "succeeded"
    assert output.artifact_refs[0].artifact_id == "artifact-code-001"


def test_waiting_user_output_must_recommend_ask_user() -> None:
    with pytest.raises(ValidationError, match="ask_user"):
        MainAgentEpisodeOutput(
            task_id="task-001",
            main_agent_run_id="main-agent-run-001",
            final_task_status="waiting_user",
            next_recommended_action="none",
            summary="Waiting for user.",
        )
