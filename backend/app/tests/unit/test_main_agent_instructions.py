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

    assert "Codex-like backend execution agent" in instructions
    assert "list_files" in instructions
    assert "read_file" in instructions
    assert "write_file" in instructions
    assert "apply_patch" in instructions
    assert "exec_command" in instructions
    assert "Call finish_task" in instructions or "finish_task" in instructions
    assert "Assistant text alone never completes the task" in instructions
    assert "Runtime policy is authoritative" in instructions


def test_orchestration_instructions_cover_artifact_boundaries() -> None:
    instructions = build_orchestration_instructions()

    assert "write_artifact" in instructions
    assert "large code" in instructions
    assert "command output" in instructions
    assert "read_artifact" in instructions


def test_orchestration_instructions_cover_mcp_domain_tools() -> None:
    instructions = build_orchestration_instructions()

    assert "call_mcp_tool" in instructions
    assert "PLC workers are" in instructions
    assert "not the default execution path" in instructions


def test_state_view_prompt_wraps_compact_state() -> None:
    prompt = build_state_view_prompt({"task_id": "task-001"})

    assert "compact Router task state view" in prompt
    assert "artifact references and summaries only" in prompt
    assert "task-001" in prompt
