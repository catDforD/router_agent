import pytest
from pydantic import ValidationError

from app.agents.output_schema import (
    IntakeClassificationOutput,
    IntakeClarificationQuestion,
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
    MainAgentPlanStep,
)


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


def classification(**updates: object) -> IntakeClassificationOutput:
    values: dict[str, object] = {
        "normalized_goal": "Create a motor start/stop routine.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.5,
        "difficulty_confidence": 0.8,
        "difficulty_reasons": ["Requires PLC development."],
        "difficulty_signals": signals(has_io_points=True),
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }
    values.update(updates)
    return IntakeClassificationOutput.model_validate(values)


def test_valid_qa_classification_output() -> None:
    output = classification(
        task_type="qa",
        difficulty_level="L1",
        difficulty_reasons=["Explanation-only task."],
        requires_test=False,
        requires_formal=False,
    )

    assert output.task_type == "qa"
    assert output.requires_test is False
    assert output.requires_formal is False


def test_valid_l2_development_classification_output() -> None:
    output = classification()

    assert output.task_type == "new_plc_development"
    assert output.difficulty_level == "L2"
    assert output.requires_test is True


def test_classification_clamps_probability_like_model_scores() -> None:
    output = classification(difficulty_score=2.1, difficulty_confidence="1.2")

    assert output.difficulty_score == 1.0
    assert output.difficulty_confidence == 1.0


def test_valid_l3_safety_classification_output() -> None:
    output = classification(
        difficulty_level="L3",
        difficulty_reasons=["Emergency stop requires formal verification."],
        difficulty_signals=signals(has_emergency_stop=True),
        requires_formal=True,
        requires_repair_loop=True,
    )

    assert output.difficulty_signals.has_emergency_stop is True
    assert output.requires_formal is True


def test_valid_repair_classification_output() -> None:
    output = classification(
        task_type="repair_existing_code",
        difficulty_level="L3",
        difficulty_reasons=["Existing code has a blocking failure."],
        difficulty_signals=signals(has_existing_code=True),
        requires_repair_loop=True,
    )

    assert output.task_type == "repair_existing_code"
    assert output.requires_repair_loop is True


def test_valid_clarification_required_classification_output() -> None:
    output = classification(
        difficulty_level="L1",
        difficulty_reasons=["Missing platform and I/O names."],
        difficulty_signals=signals(requirement_incomplete=True),
        requires_test=False,
        need_clarification=True,
        clarification_questions=[
            {
                "question": "Which PLC platform should be targeted?",
                "reason": "The platform changes generated code conventions.",
                "required": True,
            }
        ],
    )

    assert output.need_clarification is True
    assert output.clarification_questions[0].required is True


def test_classification_rejects_clarification_without_questions() -> None:
    with pytest.raises(ValidationError, match="clarification_questions"):
        classification(need_clarification=True, clarification_questions=[])


def test_classification_rejects_unknown_task_type() -> None:
    with pytest.raises(ValidationError, match="must not be unknown"):
        classification(task_type="unknown")


def test_classification_rejects_invalid_enum_value() -> None:
    with pytest.raises(ValidationError):
        classification(task_type="invalid-task-type")


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


def test_intake_clarification_question_rejects_blank_question() -> None:
    with pytest.raises(ValidationError):
        IntakeClarificationQuestion(
            question="",
            reason="Missing platform.",
            required=True,
        )
