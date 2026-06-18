import json
from pathlib import Path
from typing import Any

import pytest

from app.mcp.draft import (
    LlmWorkerDraftOutput,
    McpDraftValidationError,
    McpWorkerRequest,
    parse_worker_draft_output,
    validate_worker_draft_output,
    validate_worker_request_tool,
)
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ExpectedOutputSpec,
    TaskState,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def artifact_ref(artifact_id: str, artifact_type: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"artifact-{artifact_id}",
        type=artifact_type,
        version=1,
        uri=f"local://artifacts/task-001/artifact-{artifact_id}",
        summary=f"{artifact_type} artifact",
    )


def artifact_write(
    artifact_type: str,
    name: str,
    content: Any,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "version": 1,
        "name": name,
        "content": content,
        "summary": f"{artifact_type} summary",
        "mime_type": "application/json" if isinstance(content, dict) else "text/plain",
    }


def test_valid_plc_dev_draft_requires_requirements_code_and_io_contract() -> None:
    worker_input = build_worker_input(WorkerType.PLC_DEV, [artifact_ref("raw", "raw_user_request")])
    draft = parse_worker_draft_output(
        {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Generated PLC code.",
            "artifact_writes": [
                artifact_write("requirements_ir", "requirements_ir_v1.json", {"requirements": []}),
                artifact_write("plc_code", "plc_code_v1.st", "PROGRAM Main\nEND_PROGRAM"),
                artifact_write("io_contract", "io_contract_v1.json", {"inputs": [], "outputs": []}),
            ],
            "next_recommended_action": "test",
        }
    )

    validate_worker_draft_output(draft, worker_input)


@pytest.mark.parametrize(
    ("worker_type", "input_artifacts", "artifact_types", "missing"),
    [
        (
            WorkerType.PLC_DEV,
            [artifact_ref("raw", "raw_user_request")],
            ["requirements_ir", "plc_code"],
            "io_contract",
        ),
        (
            WorkerType.PLC_TEST,
            [
                artifact_ref("requirements", "requirements_ir"),
                artifact_ref("code", "plc_code"),
            ],
            [],
            "test_report",
        ),
        (
            WorkerType.PLC_FORMAL,
            [
                artifact_ref("requirements", "requirements_ir"),
                artifact_ref("code", "plc_code"),
            ],
            [],
            "formal_report",
        ),
        (
            WorkerType.PLC_REPAIR,
            [
                artifact_ref("code", "plc_code"),
                artifact_ref("report", "test_report"),
            ],
            ["patch", "plc_code"],
            "repair_summary",
        ),
    ],
)
def test_passed_draft_rejects_missing_required_artifacts(
    worker_type: WorkerType,
    input_artifacts: list[ArtifactRef],
    artifact_types: list[str],
    missing: str,
) -> None:
    worker_input = build_worker_input(worker_type, input_artifacts)
    draft = LlmWorkerDraftOutput.model_validate(
        {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Passed but incomplete.",
            "artifact_writes": [
                artifact_write(artifact_type, f"{artifact_type}.txt", "content")
                for artifact_type in artifact_types
            ],
            "next_recommended_action": "none",
        }
    )

    with pytest.raises(McpDraftValidationError) as exc_info:
        validate_worker_draft_output(draft, worker_input)

    assert missing in exc_info.value.details["missing_artifact_types"]


def test_tool_worker_mismatch_is_rejected() -> None:
    worker_input = build_worker_input(WorkerType.PLC_DEV, [artifact_ref("raw", "raw_user_request")])
    request = McpWorkerRequest(worker_input=worker_input, input_artifacts=[])

    with pytest.raises(McpDraftValidationError, match="worker_type does not match"):
        validate_worker_request_tool(request, "plc_test.run")


def test_malformed_json_text_is_rejected() -> None:
    with pytest.raises(McpDraftValidationError, match="not valid JSON"):
        parse_worker_draft_output("{not-json")


def test_fenced_json_text_is_parsed() -> None:
    draft = parse_worker_draft_output(
        """```json
{"outcome":{"status":"failed","blocking":true},"summary":"Failed","next_recommended_action":"repair"}
```"""
    )

    assert draft.outcome.status == "failed"
    assert draft.next_recommended_action == "repair"


def test_failed_outcome_can_omit_passed_required_artifacts() -> None:
    worker_input = build_worker_input(
        WorkerType.PLC_TEST,
        [artifact_ref("requirements", "requirements_ir"), artifact_ref("code", "plc_code")],
    )
    draft = LlmWorkerDraftOutput.model_validate(
        {
            "outcome": {"status": "failed", "blocking": True, "confidence": 0.8},
            "summary": "Test failed.",
            "artifact_writes": [],
            "next_recommended_action": "repair",
        }
    )

    validate_worker_draft_output(draft, worker_input)


def test_need_clarification_requires_clarification_request() -> None:
    worker_input = build_worker_input(WorkerType.PLC_DEV, [artifact_ref("raw", "raw_user_request")])
    draft = LlmWorkerDraftOutput.model_validate(
        {
            "outcome": {"status": "need_clarification", "blocking": True},
            "summary": "Need more details.",
            "next_recommended_action": "ask_user",
        }
    )

    with pytest.raises(McpDraftValidationError, match="clarification_request"):
        validate_worker_draft_output(draft, worker_input)


def build_worker_input(
    worker_type: WorkerType,
    input_artifacts: list[ArtifactRef],
) -> WorkerInput:
    task = TaskState.model_validate(
        json.loads((FIXTURE_DIR / "task_state.valid.json").read_text(encoding="utf-8"))
    )
    worker = worker_type.value
    return WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id=f"worker-job-{worker}-001",
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode={
            WorkerType.PLC_DEV.value: WorkerMode.CREATE,
            WorkerType.PLC_TEST.value: WorkerMode.TEST,
            WorkerType.PLC_FORMAL.value: WorkerMode.FORMAL_VERIFY,
            WorkerType.PLC_REPAIR.value: WorkerMode.REPAIR,
        }[worker],
        objective=f"Run {worker}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=task.runtime_limits.repair_rounds,
            assumptions=[],
        ),
        constraints=[],
        expected_outputs=[
            ExpectedOutputSpec(
                artifact_type=artifact_type,
                required=True,
                description=f"Expected {artifact_type.value}.",
            )
            for artifact_type in {
                WorkerType.PLC_DEV: [
                    ArtifactType.PLC_CODE,
                    ArtifactType.IO_CONTRACT,
                ],
                WorkerType.PLC_TEST: [ArtifactType.TEST_REPORT],
                WorkerType.PLC_FORMAL: [ArtifactType.FORMAL_REPORT],
                WorkerType.PLC_REPAIR: [
                    ArtifactType.PATCH,
                    ArtifactType.PLC_CODE,
                    ArtifactType.REPAIR_SUMMARY,
                ],
            }[worker_type]
        ],
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=f"worker-job-{worker}-001"),
        idempotency_key=f"{task.task_id}:worker-job-{worker}-001",
        created_at=task.created_at,
    )
