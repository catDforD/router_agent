from app.agents.instructions import (
    build_intake_instructions,
    build_orchestration_instructions,
    build_state_view_prompt,
)


def test_intake_instructions_cover_classification_policy() -> None:
    instructions = build_intake_instructions()

    assert "never return unknown" in instructions
    assert "requires_test=true for L2, L3, or L4" in instructions
    assert "requires_formal=true" in instructions
    assert "need_clarification=true" in instructions
    assert "Do not call PLC worker tools" in instructions


def test_orchestration_instructions_cover_guarded_finalization() -> None:
    instructions = build_orchestration_instructions()

    assert "Start by calling update_plan" in instructions
    assert "Always run run_quality_gate before successful completion" in instructions
    assert "call write_final_report" in instructions
    assert "Then call finish_task" in instructions
    assert "Assistant text alone never completes the task" in instructions
    assert "Do not recommend success" in instructions
    assert "guard violations" in instructions
    assert "blocking failures" in instructions
    assert "pending regression" in instructions
    assert "rationale_summary" in instructions
    assert "not hidden chain-of-thought" in instructions


def test_orchestration_instructions_cover_artifact_boundaries() -> None:
    instructions = build_orchestration_instructions()

    assert "Large content stays in artifacts" in instructions
    assert "Do not copy full PLC code" in instructions
    assert "full test logs" in instructions
    assert "artifact IDs" in instructions
    assert "read_artifact" in instructions


def test_orchestration_instructions_cover_test_formal_and_repair_policy() -> None:
    instructions = build_orchestration_instructions()

    assert "L2 or higher development must call call_plc_test" in instructions
    assert "must call call_plc_formal" in instructions
    assert "call_plc_repair" in instructions
    assert "run formal regression" in instructions
    assert "maximum repair rounds" in instructions


def test_state_view_prompt_wraps_compact_state() -> None:
    prompt = build_state_view_prompt({"task_id": "task-001"})

    assert "compact Router task state view" in prompt
    assert "artifact references and summaries only" in prompt
    assert "task-001" in prompt
