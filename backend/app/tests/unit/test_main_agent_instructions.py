from app.agents.instructions import (
    build_orchestration_instructions,
    build_state_view_prompt,
)


def test_orchestration_instructions_cover_guarded_finalization() -> None:
    instructions = build_orchestration_instructions()

    assert "Codex-like backend execution agent" in instructions
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
    assert "direct worker parameters" in instructions


def test_state_view_prompt_wraps_compact_state() -> None:
    prompt = build_state_view_prompt({"task_id": "task-001"})

    assert "compact Router task state view" in prompt
    assert "workspace file paths and summaries only" in prompt
    assert "task-001" in prompt
