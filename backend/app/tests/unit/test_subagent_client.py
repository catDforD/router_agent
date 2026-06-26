import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.mcp.draft import McpInputArtifactSnapshot, validate_worker_draft_output
from app.mcp.subagent_client import (
    SubagentConnectionError,
    SubagentExecutionError,
    SubagentWorkerClient,
    build_subagent_request,
    draft_from_subagent_events,
    parse_sse_events,
)
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ExpectedOutputSpec,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
)


def test_parse_sse_events_reads_json_data_and_ignores_keepalive() -> None:
    events = parse_sse_events(
        [
            ": keepalive",
            "",
            "data: {\"type\":\"session_id\",\"session_id\":\"s-1\"}",
            "",
            "event: token",
            "data: {\"type\":\"token\",\"content\":\"hello\"}",
            "",
            "data: {\"type\":\"workflow_end\",\"status\":\"success\"}",
            "",
        ]
    )

    assert [event["type"] for event in events] == [
        "session_id",
        "token",
        "workflow_end",
    ]
    assert events[1]["event"] == "token"
    assert events[1]["data"]["content"] == "hello"


@pytest.mark.parametrize(
    ("worker_type", "worker_config", "expected_agent", "expected_context"),
    [
        (
            WorkerType.PLC_DEV,
            {
                "target_language": "FBD",
                "template": "start_stop",
                "language_hint": "Use PLCopen XML",
                "enable_socratic_spec": True,
                "socratic_skip": False,
                "compiler_type": "matiec",
                "rpc_pipeline": ["fuzz", "formal"],
                "llm": {"model": "dev-model", "timeout_seconds": 30},
            },
            "retrieval_planning_coding_agent",
            {
                "target_language": "FBD",
                "template": "start_stop",
                "language_hint": "Use PLCopen XML",
                "enable_socratic_spec": True,
                "socratic_skip": False,
                "compiler_type": "matiec",
                "rpc_pipeline": ["fuzz", "formal"],
                "llm": {"model": "dev-model", "timeout_seconds": 30},
            },
        ),
        (
            WorkerType.PLC_TEST,
            {
                "fuzz_method": "llm",
                "case_count": 7,
                "enable_fuzz_test": False,
                "llm": {"model": "test-model"},
            },
            "fuzz_testing_agent",
            {
                "fuzz_method": "llm",
                "case_count": 7,
                "enable_fuzz_test": False,
                "llm": {"model": "test-model"},
            },
        ),
        (
            WorkerType.PLC_FORMAL,
            {
                "compiler_type": "rusty",
                "properties": [{"property": "G(Output)"}],
                "natural_language_requirements": "Output must remain safe.",
                "llm": {"temperature": 0},
            },
            "formal_validation_agent",
            {
                "compiler_type": "rusty",
                "properties": [{"property": "G(Output)"}],
                "natural_language_requirements": "Output must remain safe.",
                "llm": {"temperature": 0.0},
            },
        ),
        (
            WorkerType.PLC_REPAIR,
            {
                "repair_source": "test_failure",
                "repair_targets": ["test_failure"],
                "repair_failure_notes": "tc_023 failed",
                "compiler_type": "rusty",
                "llm": {"max_retries": 1},
            },
            "compilation_debugging_agent",
            {
                "repair_source": "test_failure",
                "repair_targets": ["test_failure"],
                "repair_failure_notes": "tc_023 failed",
                "compiler_type": "rusty",
                "llm": {"max_retries": 1},
            },
        ),
    ],
)
def test_build_subagent_request_maps_worker_config_to_context(
    worker_type: WorkerType,
    worker_config: dict[str, Any],
    expected_agent: str,
    expected_context: dict[str, Any],
) -> None:
    request = build_subagent_request(
        worker_input(worker_type, worker_config=worker_config),
        snapshots(worker_type),
        artifact_max_chars=80,
    )

    assert request["agent_id"] == expected_agent
    assert request["context"] == expected_context
    assert "Run" in request["message"]
    assert "Input artifacts:" in request["message"]


def test_dev_structured_code_converts_to_passed_draft() -> None:
    payload = worker_input(WorkerType.PLC_DEV, worker_config={"rpc_pipeline": ["fuzz"]})
    events = parse_sse_events(
        [
            "data: {\"type\":\"token\",\"content\":\"generating\"}",
            "",
            "data: "
            + json.dumps(
                {
                    "type": "st_code_json",
                    "stCode": {
                        "code": sample_code(),
                        "file_name": "motor_control.st",
                        "language": "ST",
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_DEV), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "passed"
    assert [write.artifact_type for write in draft.artifact_writes] == [
        "requirements_ir",
        "plc_code",
        "io_contract",
    ]
    assert draft.next_recommended_action == "test"


def test_test_structured_report_converts_to_passed_draft() -> None:
    payload = worker_input(WorkerType.PLC_TEST)
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "fuzz_report_json",
                    "content": {
                        "summary": "All fuzz cases passed.",
                        "total_cases": 3,
                        "passed_cases": 3,
                        "failed_cases": 0,
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_TEST), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "passed"
    assert draft.metrics.test_metrics is not None
    assert draft.metrics.test_metrics.failed == 0


def test_formal_structured_report_converts_to_passed_draft() -> None:
    payload = worker_input(WorkerType.PLC_FORMAL)
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "formal_report_json",
                    "content": {
                        "summary": "All properties are satisfied.",
                        "all_satisfied": True,
                        "total_properties": 2,
                        "passed_properties": 2,
                        "failed_properties": 0,
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_FORMAL), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "passed"
    assert draft.metrics.formal_metrics is not None
    assert draft.metrics.formal_metrics.failed_properties == 0


def test_repair_structured_report_and_code_convert_to_passed_draft() -> None:
    payload = worker_input(WorkerType.PLC_REPAIR)
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "compilation_report_json",
                    "content": {
                        "summary": "Compilation fixed.",
                        "compilation_success": True,
                    },
                }
            ),
            "",
            "data: "
            + json.dumps(
                {
                    "type": "st_code_json",
                    "content": {
                        "code": sample_code().replace("TRUE", "FALSE", 1),
                        "file_name": "motor_control_fixed.st",
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_REPAIR), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "passed"
    assert {write.artifact_type for write in draft.artifact_writes} == {
        "repair_summary",
        "patch",
        "plc_code",
    }


def test_unstructured_events_fall_back_to_failed_report_without_passed_claim() -> None:
    payload = worker_input(WorkerType.PLC_FORMAL)
    events = parse_sse_events(
        [
            "data: {\"type\":\"token\",\"content\":\"I need more code context.\"}",
            "",
            "data: {\"type\":\"workflow_end\",\"status\":\"success\"}",
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_FORMAL), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "failed"
    assert draft.artifact_writes[0].artifact_type == "formal_report"


def test_client_posts_stream_and_raises_error_event() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content='data: {"type":"error","message":"remote failed"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SubagentWorkerClient(
        base_url="http://subagent.example",
        api_token="secret-token",
        http_client=http_client,
    )

    with pytest.raises(SubagentExecutionError, match="remote failed"):
        client.call_worker(worker_input(WorkerType.PLC_TEST), snapshots(WorkerType.PLC_TEST))

    assert captured["url"] == "http://subagent.example/api/chat/stream"
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["body"]["agent_id"] == "fuzz_testing_agent"


def test_client_retries_transient_gateway_status_before_success() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        if len(attempts) == 1:
            return httpx.Response(502, text="bad gateway", request=request)
        return httpx.Response(
            200,
            content=(
                "data: "
                + json.dumps(
                    {
                        "type": "fuzz_report_json",
                        "content": {
                            "summary": "retry recovered",
                            "total_cases": 1,
                            "passed_cases": 1,
                            "failed_cases": 0,
                        },
                    }
                )
                + "\n\n"
            ),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SubagentWorkerClient(
        base_url="http://subagent.example",
        http_client=http_client,
        max_retries=2,
        retry_backoff_seconds=0,
    )

    draft = client.call_worker(
        worker_input(WorkerType.PLC_TEST),
        snapshots(WorkerType.PLC_TEST),
    )

    assert draft.outcome.status == "passed"
    assert len(attempts) == 2


def test_client_reports_retry_details_after_gateway_status_exhaustion() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(502, text="bad gateway", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = SubagentWorkerClient(
        base_url="http://subagent.example",
        http_client=http_client,
        max_retries=2,
        retry_backoff_seconds=0,
    )

    with pytest.raises(SubagentConnectionError) as exc_info:
        client.call_worker(
            worker_input(WorkerType.PLC_TEST),
            snapshots(WorkerType.PLC_TEST),
        )

    assert attempts == 3
    assert exc_info.value.details["status_code"] == 502
    assert exc_info.value.details["attempts"] == 3
    assert exc_info.value.details["max_retries"] == 2
    assert exc_info.value.details["retryable_status_code"] is True


def worker_input(
    worker_type: WorkerType,
    *,
    worker_config: dict[str, Any] | None = None,
) -> WorkerInput:
    worker = worker_type.value
    return WorkerInput(
        schema_version="router.v1",
        task_id="task-subagent-001",
        worker_job_id=f"worker-job-{worker}",
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode={
            WorkerType.PLC_DEV: WorkerMode.CREATE,
            WorkerType.PLC_TEST: WorkerMode.TEST,
            WorkerType.PLC_FORMAL: WorkerMode.FORMAL_VERIFY,
            WorkerType.PLC_REPAIR: WorkerMode.REPAIR,
        }[worker_type],
        objective=f"Run {worker}.",
        input_artifacts=artifact_refs(worker_type),
        context=WorkerContext(
            user_goal="Build and validate motor control logic.",
            task_type="new_plc_development",
            difficulty_level="L1",
            target_plc_language="ST",
            target_platform="Codesys",
            repair_round=0,
            assumptions=[],
        ),
        constraints=[],
        expected_outputs=expected_outputs(worker_type),
        budget=WorkerBudget(timeout_seconds=300, max_iterations=1),
        trace_context=TraceContext(worker_job_id=f"worker-job-{worker}"),
        idempotency_key=f"task-subagent-001:worker-job-{worker}",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        worker_config=worker_config,
    )


def artifact_refs(worker_type: WorkerType) -> list[ArtifactRef]:
    return [
        ArtifactRef(
            artifact_id=snapshot.artifact_id,
            type=snapshot.type,
            version=snapshot.version,
            uri=f"local://artifacts/{snapshot.artifact_id}",
            summary=snapshot.summary,
        )
        for snapshot in snapshots(worker_type)
    ]


def snapshots(worker_type: WorkerType) -> list[McpInputArtifactSnapshot]:
    raw = McpInputArtifactSnapshot(
        artifact_id="artifact-raw",
        type=ArtifactType.RAW_USER_REQUEST,
        version=1,
        summary="Raw request.",
        content="Create a motor control function block.",
        content_chars=38,
        mime_type="text/plain",
    )
    requirements = McpInputArtifactSnapshot(
        artifact_id="artifact-req",
        type=ArtifactType.REQUIREMENTS_IR,
        version=1,
        summary="Requirements.",
        content=json.dumps({"requirements": ["Stop button disables motor."]}),
        content_chars=48,
        mime_type="application/json",
    )
    code = McpInputArtifactSnapshot(
        artifact_id="artifact-code",
        type=ArtifactType.PLC_CODE,
        version=1,
        summary="PLC code.",
        content=sample_code(),
        content_chars=len(sample_code()),
        mime_type="text/plain",
    )
    report = McpInputArtifactSnapshot(
        artifact_id="artifact-report",
        type=ArtifactType.TEST_REPORT,
        version=1,
        summary="Failed test report.",
        content=json.dumps({"status": "failed", "failed_case": "tc_023"}),
        content_chars=46,
        mime_type="application/json",
    )
    if worker_type == WorkerType.PLC_DEV:
        return [raw]
    if worker_type in {WorkerType.PLC_TEST, WorkerType.PLC_FORMAL}:
        return [requirements, code]
    return [code, report]


def expected_outputs(worker_type: WorkerType) -> list[ExpectedOutputSpec]:
    outputs = {
        WorkerType.PLC_DEV: [
            ArtifactType.REQUIREMENTS_IR,
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
    return [
        ExpectedOutputSpec(
            artifact_type=artifact_type,
            required=True,
            description=f"Expected {artifact_type.value}.",
        )
        for artifact_type in outputs
    ]


def sample_code() -> str:
    return (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartBtn : BOOL;\n"
        "    StopBtn : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "END_VAR\n"
        "IF StopBtn THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartBtn THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "END_FUNCTION_BLOCK\n"
    )
