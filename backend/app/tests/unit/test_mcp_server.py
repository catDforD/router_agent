import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.mcp.llm_worker import DeepSeekConfigurationError, DeepSeekChatClient, LlmPlcWorkerService
from app.mcp.server import create_plc_worker_mcp_server
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


def artifact_ref(artifact_id: str, artifact_type: ArtifactType) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"artifact-{artifact_id}",
        type=artifact_type,
        version=1,
        uri=f"local://artifacts/task-001/artifact-{artifact_id}",
        summary=f"{artifact_type.value} artifact",
    )


def artifact_write(
    artifact_type: str,
    name: str,
    content: Any,
    *,
    version: int = 1,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "version": version,
        "name": name,
        "content": content,
        "summary": f"{artifact_type} summary",
        "mime_type": "application/json" if isinstance(content, dict) else "text/plain",
    }


def test_server_lists_four_plc_worker_tools() -> None:
    server = create_plc_worker_mcp_server(service=LlmPlcWorkerService(FakeJsonClient()))

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} >= {
        "plc_dev.run",
        "plc_test.run",
        "plc_formal.run",
        "plc_repair.run",
    }


@pytest.mark.parametrize(
    ("tool_name", "worker_type", "input_artifacts", "expected_artifacts"),
    [
        (
            "plc_dev.run",
            WorkerType.PLC_DEV,
            [artifact_ref("raw", ArtifactType.RAW_USER_REQUEST)],
            {"requirements_ir", "plc_code", "io_contract"},
        ),
        (
            "plc_test.run",
            WorkerType.PLC_TEST,
            [
                artifact_ref("requirements", ArtifactType.REQUIREMENTS_IR),
                artifact_ref("code", ArtifactType.PLC_CODE),
            ],
            {"test_report"},
        ),
        (
            "plc_formal.run",
            WorkerType.PLC_FORMAL,
            [
                artifact_ref("requirements", ArtifactType.REQUIREMENTS_IR),
                artifact_ref("code", ArtifactType.PLC_CODE),
            ],
            {"formal_report"},
        ),
        (
            "plc_repair.run",
            WorkerType.PLC_REPAIR,
            [
                artifact_ref("code", ArtifactType.PLC_CODE),
                artifact_ref("report", ArtifactType.TEST_REPORT),
            ],
            {"patch", "plc_code", "repair_summary"},
        ),
    ],
)
def test_server_tools_return_valid_worker_drafts(
    tool_name: str,
    worker_type: WorkerType,
    input_artifacts: list[ArtifactRef],
    expected_artifacts: set[str],
) -> None:
    service = LlmPlcWorkerService(FakeJsonClient())
    server = create_plc_worker_mcp_server(service=service)
    worker_input = build_worker_input(worker_type, input_artifacts)

    result = asyncio.run(
        server.call_tool(
            tool_name,
            {
                "worker_input": worker_input.model_dump(mode="json"),
                "input_artifacts": [],
            },
        )
    )

    result = structured_payload(result)

    assert result["outcome"]["status"] == "passed"
    assert {
        artifact["artifact_type"]
        for artifact in result["artifact_writes"]
    } == expected_artifacts
    assert result["metadata"]["worker_simulation"] == "deepseek_openai_compatible"
    assert result["metadata"]["mcp_tool"] == tool_name


def test_server_rejects_worker_type_tool_mismatch() -> None:
    service = LlmPlcWorkerService(FakeJsonClient())
    server = create_plc_worker_mcp_server(service=service)
    worker_input = build_worker_input(
        WorkerType.PLC_DEV,
        [artifact_ref("raw", ArtifactType.RAW_USER_REQUEST)],
    )

    with pytest.raises(Exception, match="worker_type does not match"):
        asyncio.run(
            server.call_tool(
                "plc_test.run",
                {
                    "worker_input": worker_input.model_dump(mode="json"),
                    "input_artifacts": [],
                },
            )
        )


def test_service_coerces_router_like_llm_output_to_worker_draft() -> None:
    service = LlmPlcWorkerService(RouterLikeJsonClient())
    worker_input = build_worker_input(
        WorkerType.PLC_DEV,
        [artifact_ref("raw", ArtifactType.RAW_USER_REQUEST)],
    )

    result = service.run_tool(
        "plc_dev.run",
        {
            "worker_input": worker_input.model_dump(mode="json"),
            "input_artifacts": [],
        },
    )

    assert result["outcome"]["status"] == "passed"
    assert result["outcome"]["blocking"] is False
    assert {
        artifact["artifact_type"]
        for artifact in result["artifact_writes"]
    } == {"requirements_ir", "plc_code", "io_contract"}
    assert all("artifact_id" not in artifact for artifact in result["artifact_writes"])
    assert all("type" not in artifact for artifact in result["artifact_writes"])
    plc_code = next(
        artifact
        for artifact in result["artifact_writes"]
        if artifact["artifact_type"] == "plc_code"
    )
    assert plc_code["metadata"]["tags"] == [
        "generated_by:plc_dev_subagent"
    ]
    assert result["assumptions"][0]["source"] == "plc-dev"
    assert result["diagnostics"][0]["severity"] == "info"
    assert result["diagnostics"][0]["message"] == "Generated ST code conforms to IEC 61131-3."
    assert "component" not in result["diagnostics"][0]
    assert result["metrics"]["duration_ms"] == 1200
    assert result["metrics"]["token_usage"]["total_tokens"] == 450


def test_service_falls_back_when_llm_output_remains_invalid() -> None:
    service = LlmPlcWorkerService(InvalidDraftJsonClient())
    worker_input = build_worker_input(
        WorkerType.PLC_DEV,
        [artifact_ref("raw", ArtifactType.RAW_USER_REQUEST)],
    )

    result = service.run_tool(
        "plc_dev.run",
        {
            "worker_input": worker_input.model_dump(mode="json"),
            "input_artifacts": [],
        },
    )

    assert result["outcome"]["status"] == "passed"
    assert {
        artifact["artifact_type"]
        for artifact in result["artifact_writes"]
    } == {"requirements_ir", "plc_code", "io_contract"}
    assert result["metadata"]["llm_output_fallback"] is True
    assert result["diagnostics"][0]["code"] == "LLM_INVALID_DRAFT_FALLBACK"
    assert result["artifact_writes"][0]["metadata"]["tags"] == ["llm-output-fallback"]


def test_deepseek_client_requires_api_key() -> None:
    client = DeepSeekChatClient(
        api_key=None,
        base_url="https://deepseek.example/v1",
        model="deepseek-worker",
    )

    with pytest.raises(DeepSeekConfigurationError):
        client.complete_json(system_prompt="system", user_prompt="user")


class FakeJsonClient:
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        payload = json.loads(user_prompt)
        return draft_for_tool(payload["tool_name"])


class RouterLikeJsonClient:
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        return {
            "outcome": "passed",
            "summary": "Generated PLC code.",
            "artifact_writes": [
                {
                    "artifact_id": "artifact-requirements-ir-001",
                    "type": "requirements_ir",
                    "version": 1,
                    "uri": "generated://artifacts/requirements_ir_v1.json",
                    "content": {"requirements": [{"id": "REQ-1", "text": "Motor must stop safely."}]},
                    "summary": "Generated requirements IR.",
                    "content_truncated": False,
                    "mime_type": "application/json",
                },
                {
                    "artifact_id": "artifact-plc-code-001",
                    "type": "plc_code",
                    "version": 1,
                    "uri": "generated://artifacts/plc_code_v1.st",
                    "content": "PROGRAM Main\nEND_PROGRAM",
                    "summary": "Generated ST code.",
                    "content_truncated": False,
                    "mime_type": "text/plain",
                    "metadata": {"generated_by": "plc_dev_subagent"},
                },
                {
                    "artifact_id": "artifact-io-contract-001",
                    "type": "io_contract",
                    "version": 1,
                    "uri": "generated://artifacts/io_contract_v1.json",
                    "content": {"inputs": [], "outputs": []},
                    "summary": "Generated IO contract.",
                    "content_truncated": False,
                    "mime_type": "application/json",
                },
            ],
            "assumptions": [
                {
                    "assumption_id": "assumption-001",
                    "text": "Structured Text is acceptable.",
                    "source": "plc_dev_agent",
                    "confidence": 0.8,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "diagnostics": [
                {
                    "message": "Generated ST code conforms to IEC 61131-3.",
                    "component": "plc-dev",
                }
            ],
            "metrics": {
                "processing_time_seconds": 1.2,
                "token_count": 450,
                "model": "gpt-4-mini",
            },
            "next_recommended_action": "test",
        }


class InvalidDraftJsonClient:
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        return {"outcome": "passed", "summary": "Missing required draft fields."}


def structured_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    assert isinstance(result, dict)
    return result


def draft_for_tool(tool_name: str) -> dict[str, Any]:
    if tool_name == "plc_dev.run":
        return {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Generated PLC code.",
            "artifact_writes": [
                artifact_write("requirements_ir", "requirements_ir_v1.json", {"requirements": []}),
                artifact_write("plc_code", "plc_code_v1.st", "PROGRAM Main\nEND_PROGRAM"),
                artifact_write("io_contract", "io_contract_v1.json", {"inputs": [], "outputs": []}),
            ],
            "next_recommended_action": "test",
        }
    if tool_name == "plc_test.run":
        return {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Tests passed.",
            "artifact_writes": [
                artifact_write("test_report", "test_report.json", {"status": "passed"}),
            ],
            "metrics": {"test_metrics": {"total": 1, "passed": 1, "failed": 0}},
            "next_recommended_action": "run_quality_gate",
        }
    if tool_name == "plc_formal.run":
        return {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Formal checks passed.",
            "artifact_writes": [
                artifact_write("formal_report", "formal_report.json", {"status": "passed"}),
            ],
            "metrics": {"formal_metrics": {"total_properties": 1, "passed_properties": 1}},
            "next_recommended_action": "run_quality_gate",
        }
    if tool_name == "plc_repair.run":
        return {
            "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
            "summary": "Repaired PLC code.",
            "artifact_writes": [
                artifact_write("patch", "patch_v1.diff", "--- a\n+++ b\n"),
                artifact_write("plc_code", "plc_code_v2.st", "PROGRAM Main\nEND_PROGRAM", version=2),
                artifact_write("repair_summary", "repair_summary_v1.json", {"repair_round": 1}),
            ],
            "metrics": {"repair_metrics": {"changed_files": 1, "changed_lines": 2}},
            "next_recommended_action": "test",
        }
    raise AssertionError(f"unexpected tool: {tool_name}")


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
            WorkerType.PLC_DEV: WorkerMode.CREATE,
            WorkerType.PLC_TEST: WorkerMode.TEST,
            WorkerType.PLC_FORMAL: WorkerMode.FORMAL_VERIFY,
            WorkerType.PLC_REPAIR: WorkerMode.REPAIR,
        }[worker_type],
        objective=f"Run {worker}.",
        input_artifacts=input_artifacts,
        context=WorkerContext(
            user_goal=task.raw_user_request,
            task_type=task.task_type,
            difficulty_level=task.difficulty.level,
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=0,
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
                WorkerType.PLC_DEV: [ArtifactType.PLC_CODE, ArtifactType.IO_CONTRACT],
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
