from app.agents.instructions import (
    build_orchestration_instructions,
    build_state_view_prompt,
)


def test_orchestration_instructions_cover_guarded_finalization() -> None:
    instructions = build_orchestration_instructions()

    assert "backend execution agent" in instructions
    assert "list_files" in instructions
    assert "read_file" in instructions
    assert "write_file" in instructions
    assert "apply_patch" in instructions
    assert "exec_command" in instructions
    assert "finish_task" not in instructions
    assert "return the final answer as normal" in instructions
    assert "assistant text with no tool calls" in instructions
    assert "Do not add" in instructions
    assert "Runtime policy is" in instructions
    assert "authoritative" in instructions


def test_orchestration_instructions_cover_workspace_file_boundaries() -> None:
    instructions = build_orchestration_instructions()

    assert "workspace files" in instructions
    assert ".router/runs" in instructions
    assert ".router/reports" in instructions
    assert "large code" in instructions
    assert "command output" in instructions
    assert "read_artifact" not in instructions
    assert "write_artifact" not in instructions


def test_orchestration_instructions_cover_mcp_domain_tools() -> None:
    instructions = build_orchestration_instructions()

    assert "call_mcp_tool" not in instructions
    assert "plc_dev" in instructions
    assert "plc_test" in instructions
    assert "plc_formal" in instructions
    assert "plc_repair" in instructions
    assert "Do not invent alternative PLC worker tool names" in instructions
    assert "call_plc_dev" not in instructions
    assert "call_plc_test" not in instructions
    assert "call_plc_formal" not in instructions
    assert "call_plc_repair" not in instructions


def test_orchestration_instructions_prioritize_clarification_before_workers() -> None:
    instructions = build_orchestration_instructions()

    assert "Clarification has priority over dispatch" in instructions
    assert "request_clarification immediately" in instructions
    assert "Do not call update_plan" in instructions
    assert "any PLC worker before the user answers" in instructions


def test_orchestration_instructions_define_plc_route_table() -> None:
    instructions = build_orchestration_instructions()

    assert "PLC route table" in instructions
    assert "ordinary new development" in instructions
    assert "new development with safety" in instructions
    assert "test existing code only" in instructions
    assert "formal verification of existing code only" in instructions
    assert "repair after failing test evidence" in instructions
    assert "repair after formal failure or counterexample evidence" in instructions
    assert "plc_dev -> plc_test" in instructions
    assert "plc_test -> plc_repair" in instructions
    assert "plc_formal -> plc_repair" in instructions
    assert "Do not call a worker outside the selected" in instructions


def test_orchestration_instructions_require_formal_properties() -> None:
    instructions = build_orchestration_instructions()

    assert "Before calling plc_formal" in instructions
    assert "generate structured properties" in instructions
    assert "pass them through the properties" in instructions
    assert "Do not rely on natural_language_requirements alone" in instructions
    assert '"job_req":"assertion"' in instructions
    assert '"pattern_id":"pattern-invariant"' in instructions


def test_orchestration_instructions_limit_gate_and_retries() -> None:
    instructions = build_orchestration_instructions()

    assert "run_quality_gate at most once" in instructions
    assert "final answer with no tool calls" in instructions
    assert "make at most one clear recovery attempt" in instructions
    assert "looping through extra workers" in instructions


def test_state_view_prompt_wraps_compact_state() -> None:
    prompt = build_state_view_prompt({"task_id": "task-001"})

    assert "compact Router task state view" in prompt
    assert "workspace file paths and summaries only" in prompt
    assert "task-001" in prompt
