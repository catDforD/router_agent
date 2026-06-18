import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.types import CallToolResult, TextContent

from app.mcp.client import (
    PlcMcpClient,
    PlcMcpConnectionError,
    PlcMcpInvalidResponseError,
    PlcMcpTimeoutError,
    PlcMcpToolNotFoundError,
)
from app.mcp.draft import McpWorkerRequest
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


def test_list_tools_uses_session_factory() -> None:
    client = PlcMcpClient(
        url="http://test/mcp",
        session_factory=factory(FakeSession(tools=["plc_dev.run", "plc_test.run"])),
    )

    assert client.list_tools() == ["plc_dev.run", "plc_test.run"]


def test_call_worker_tool_parses_structured_content() -> None:
    session = FakeSession(
        tools=["plc_dev.run"],
        result=CallToolResult(
            content=[],
            structuredContent=valid_draft_payload(),
        ),
    )
    client = PlcMcpClient(url="http://test/mcp", session_factory=factory(session))

    draft = client.call_worker_tool("plc_dev.run", request())

    assert draft.outcome.status == "passed"
    assert [write.artifact_type for write in draft.artifact_writes] == [
        "plc_code",
        "io_contract",
    ]
    assert session.calls == [("plc_dev.run", request().model_dump(mode="json"))]


def test_call_worker_tool_parses_text_json_content() -> None:
    session = FakeSession(
        tools=["plc_dev.run"],
        result=CallToolResult(
            content=[TextContent(type="text", text=json.dumps(valid_draft_payload()))],
        ),
    )
    client = PlcMcpClient(url="http://test/mcp", session_factory=factory(session))

    draft = client.call_worker_tool("plc_dev.run", request())

    assert draft.summary == "Generated PLC code."


def test_missing_tool_is_reported() -> None:
    client = PlcMcpClient(
        url="http://test/mcp",
        session_factory=factory(FakeSession(tools=["plc_test.run"])),
    )

    with pytest.raises(PlcMcpToolNotFoundError):
        client.call_worker_tool("plc_dev.run", request())


def test_invalid_response_is_reported() -> None:
    client = PlcMcpClient(
        url="http://test/mcp",
        session_factory=factory(
            FakeSession(
                tools=["plc_dev.run"],
                result=CallToolResult(
                    content=[TextContent(type="text", text="{not-json")],
                ),
            )
        ),
    )

    with pytest.raises(PlcMcpInvalidResponseError):
        client.call_worker_tool("plc_dev.run", request())


def test_timeout_is_reported_without_secret_values() -> None:
    client = PlcMcpClient(
        url="http://test/mcp",
        session_factory=factory(
            FakeSession(
                tools=["plc_dev.run"],
                list_error=httpx.TimeoutException("timed out with api-key-secret"),
            )
        ),
    )

    with pytest.raises(PlcMcpTimeoutError) as exc_info:
        client.list_tools()

    assert "api-key-secret" not in str(exc_info.value.details)


def test_connection_failure_is_reported() -> None:
    client = PlcMcpClient(
        url="http://test/mcp",
        session_factory=factory(
            FakeSession(
                tools=["plc_dev.run"],
                list_error=httpx.ConnectError("connection failed"),
            )
        ),
    )

    with pytest.raises(PlcMcpConnectionError):
        client.list_tools()


class FakeSession:
    def __init__(
        self,
        *,
        tools: list[str],
        result: Any | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.tools = tools
        self.result = result
        self.list_error = list_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tool_names(self) -> list[str]:
        if self.list_error is not None:
            raise self.list_error
        return self.tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        self.calls.append((tool_name, arguments))
        return self.result


def factory(session: FakeSession):
    @asynccontextmanager
    async def make_session() -> AsyncIterator[FakeSession]:
        yield session

    return make_session


def valid_draft_payload() -> dict[str, Any]:
    return {
        "outcome": {"status": "passed", "blocking": False, "confidence": 0.9},
        "summary": "Generated PLC code.",
        "artifact_writes": [
            {
                "artifact_type": "plc_code",
                "version": 1,
                "name": "plc_code_v1.st",
                "content": "PROGRAM Main\nEND_PROGRAM",
                "summary": "PLC code.",
                "mime_type": "text/plain",
            },
            {
                "artifact_type": "io_contract",
                "version": 1,
                "name": "io_contract_v1.json",
                "content": {"inputs": [], "outputs": []},
                "summary": "IO contract.",
                "mime_type": "application/json",
            },
        ],
        "next_recommended_action": "test",
    }


def request() -> McpWorkerRequest:
    task = TaskState.model_validate(
        json.loads((FIXTURE_DIR / "task_state.valid.json").read_text(encoding="utf-8"))
    )
    worker = WorkerType.PLC_DEV.value
    worker_input = WorkerInput(
        schema_version="router.v1",
        task_id=task.task_id,
        worker_job_id="worker-job-plc-dev-001",
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode=WorkerMode.CREATE,
        objective="Run plc-dev.",
        input_artifacts=[
            ArtifactRef(
                artifact_id="artifact-raw",
                type=ArtifactType.RAW_USER_REQUEST,
                version=1,
                uri="local://artifacts/raw",
                summary="Raw request.",
            )
        ],
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
                artifact_type=ArtifactType.PLC_CODE,
                required=True,
                description="Expected PLC code.",
            )
        ],
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id="worker-job-plc-dev-001"),
        idempotency_key=f"{task.task_id}:worker-job-plc-dev-001",
        created_at=task.created_at,
    )
    return McpWorkerRequest(worker_input=worker_input, input_artifacts=[])
