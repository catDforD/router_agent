import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.mcp.draft import McpInputFileSnapshot, validate_worker_draft_output
from app.mcp.subagent_client import (
    SubagentConnectionError,
    SubagentExecutionError,
    SubagentInvalidResponseError,
    SubagentWorkerClient,
    build_subagent_request,
    draft_from_subagent_events,
    parse_sse_events,
)
from app.models.router_schema import (
    ArtifactType,
    ExpectedFileSpec,
    TraceContext,
    WORKER_TOOL_BY_TYPE,
    WorkerBudget,
    WorkerContext,
    WorkerInput,
    WorkerMode,
    WorkerType,
)


FORMAL_PROPERTIES = [
    {
        "property_description": "Output must remain safe.",
        "property": {"job_req": "assertion"},
    }
]


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
                "properties": FORMAL_PROPERTIES,
                "natural_language_requirements": "Output must remain safe.",
                "llm": {"temperature": 0},
            },
            "formal_validation_agent",
            {
                "compiler_type": "rusty",
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
    if worker_type == WorkerType.PLC_FORMAL:
        message = json.loads(request["message"])
        assert message == {
            "st_code": sample_code(),
            "properties": FORMAL_PROPERTIES,
        }
        assert "Input files:" not in request["message"]
    elif worker_type == WorkerType.PLC_TEST:
        assert request["message"] == sample_code()
        assert "Input files:" not in request["message"]
    else:
        assert "Run" in request["message"]
        assert "Input files:" in request["message"]


def test_formal_subagent_request_requires_properties() -> None:
    with pytest.raises(SubagentInvalidResponseError, match="worker_config.properties"):
        build_subagent_request(
            worker_input(WorkerType.PLC_FORMAL),
            snapshots(WorkerType.PLC_FORMAL),
        )


def test_formal_subagent_request_rejects_truncated_code() -> None:
    input_files = [
        snapshot.model_copy(update={"content_truncated": True})
        if snapshot.type == ArtifactType.PLC_CODE.value
        else snapshot
        for snapshot in snapshots(WorkerType.PLC_FORMAL)
    ]

    with pytest.raises(SubagentInvalidResponseError, match="complete PLC code"):
        build_subagent_request(
            worker_input(
                WorkerType.PLC_FORMAL,
                worker_config={"properties": FORMAL_PROPERTIES},
            ),
            input_files,
        )


def test_test_subagent_request_rejects_truncated_code() -> None:
    input_files = [
        snapshot.model_copy(update={"content_truncated": True})
        if snapshot.type == ArtifactType.PLC_CODE.value
        else snapshot
        for snapshot in snapshots(WorkerType.PLC_TEST)
    ]

    with pytest.raises(SubagentInvalidResponseError, match="complete PLC code"):
        build_subagent_request(
            worker_input(WorkerType.PLC_TEST),
            input_files,
        )


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


def test_formal_not_checked_report_converts_to_failed_draft_with_failure() -> None:
    payload = worker_input(WorkerType.PLC_FORMAL)
    events = parse_sse_events(
        [
            "data: "
            + json.dumps(
                {
                    "type": "formal_report_json",
                    "content": {
                        "all_satisfied": False,
                        "property_count": 1,
                        "passed": 0,
                        "failed": 0,
                        "not_checked": 1,
                        "properties": [
                            {
                                "property_index": 1,
                                "status": "NOT_CHECKED",
                                "property_description": "Motor must stop.",
                                "fallback_reason": "no_suitable_files",
                                "not_checked_reason": "timeout",
                            }
                        ],
                    },
                }
            ),
            "",
        ]
    )

    draft = draft_from_subagent_events(payload, snapshots(WorkerType.PLC_FORMAL), events)

    validate_worker_draft_output(draft, payload)
    assert draft.outcome.status == "failed"
    assert draft.next_recommended_action == "repair"
    assert draft.metrics.formal_metrics is not None
    assert draft.metrics.formal_metrics.failed_properties == 0
    assert draft.metrics.formal_metrics.unknown_properties == 1
    assert len(draft.failures) == 1
    assert draft.failures[0].source == "formal"
    assert "NOT_CHECKED" in draft.failures[0].description
    assert "timeout" in draft.failures[0].description


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
    input_files = snapshots(worker_type)
    return WorkerInput(
        schema_version="router.v2",
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
        workspace_root="/tmp/router-subagent-test",
        current_directory="/tmp/router-subagent-test",
        input_paths=[snapshot.path for snapshot in input_files],
        output_paths=output_paths(worker_type),
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


def snapshots(worker_type: WorkerType) -> list[McpInputFileSnapshot]:
    raw = McpInputFileSnapshot(
        path=".router/raw_request.txt",
        type=ArtifactType.RAW_USER_REQUEST,
        version=1,
        summary="Raw request.",
        content="Create a motor control function block.",
        content_chars=38,
        mime_type="text/plain",
    )
    requirements = McpInputFileSnapshot(
        path=".router/requirements.json",
        type=ArtifactType.REQUIREMENTS_IR,
        version=1,
        summary="Requirements.",
        content=json.dumps({"requirements": ["Stop button disables motor."]}),
        content_chars=48,
        mime_type="application/json",
    )
    code = McpInputFileSnapshot(
        path="src/plc_code.st",
        type=ArtifactType.PLC_CODE,
        version=1,
        summary="PLC code.",
        content=sample_code(),
        content_chars=len(sample_code()),
        mime_type="text/plain",
    )
    report = McpInputFileSnapshot(
        path=".router/reports/test_report.json",
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


def output_paths(worker_type: WorkerType) -> list[str]:
    return {
        WorkerType.PLC_DEV: [
            "src/plc_code.st",
            ".router/reports/worker-job/requirements.json",
            ".router/reports/worker-job/io_contract.json",
        ],
        WorkerType.PLC_TEST: [".router/reports/worker-job/test_report.json"],
        WorkerType.PLC_FORMAL: [".router/reports/worker-job/formal_report.json"],
        WorkerType.PLC_REPAIR: [
            "src/plc_code.st",
            ".router/reports/worker-job/patch.diff",
            ".router/reports/worker-job/repair_summary.json",
        ],
    }[worker_type]


def expected_outputs(worker_type: WorkerType) -> list[ExpectedFileSpec]:
    return [
        ExpectedFileSpec(
            path=path,
            required=True,
            description=f"Expected {path}.",
        )
        for path in output_paths(worker_type)
    ]


def formal_properties() -> list[dict[str, Any]]:
    return FORMAL_PROPERTIES


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
