"""Opt-in live smoke call for one remote PLC HTTP subagent worker."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.mcp.draft import McpInputFileSnapshot, validate_worker_draft_output  # noqa: E402
from app.mcp.subagent_client import (  # noqa: E402
    build_subagent_request,
    draft_from_subagent_events,
    parse_sse_events,
)
from app.models.router_schema import (  # noqa: E402
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


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Call one remote PLC HTTP subagent through its SSE API.",
    )
    parser.add_argument(
        "--worker",
        required=True,
        choices=[worker.value for worker in WorkerType],
        help="Worker type to invoke.",
    )
    parser.add_argument(
        "--base-url",
        default=settings.subagent_api_base_url,
        help="Remote subagent API base URL.",
    )
    parser.add_argument(
        "--api-token",
        default=settings.subagent_api_token,
        help="Optional bearer token. Defaults to SUBAGENT_API_TOKEN.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=settings.subagent_timeout_seconds,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually call the remote subagent API.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worker_type = WorkerType(args.worker)
    payload = worker_input(worker_type)
    input_files = snapshots(worker_type)
    request = build_subagent_request(payload, input_files)
    url = f"{args.base_url.rstrip('/')}/api/chat/stream"

    print(f"worker: {worker_type.value}")
    print(f"url: {url}")
    print(f"agent_id: {request['agent_id']}")
    print("worker_config echo:")
    print(json.dumps(request["context"], indent=2, ensure_ascii=False))

    if not args.live:
        print("Refusing live subagent call without --live.")
        return 0

    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if args.api_token:
        headers["Authorization"] = f"Bearer {args.api_token}"

    with httpx.Client(timeout=httpx.Timeout(float(args.timeout))) as client:
        with client.stream("POST", url, headers=headers, json=request) as response:
            print(f"HTTP status: {response.status_code}")
            response.raise_for_status()
            events = parse_sse_events(response.iter_lines())

    event_types = [str(event.get("type")) for event in events]
    print("SSE event types:")
    print(json.dumps(event_types, indent=2, ensure_ascii=False))

    error_events = [event for event in events if event.get("type") == "error"]
    if error_events:
        print("error event:")
        print(json.dumps(error_events[0]["data"], indent=2, ensure_ascii=False))
        return 2

    draft = draft_from_subagent_events(payload, input_files, events)
    validate_worker_draft_output(draft, payload)
    print(f"outcome: {draft.outcome.status}")
    print(f"summary: {draft.summary}")
    print("artifact types:")
    print(
        json.dumps(
            [write.artifact_type for write in draft.artifact_writes],
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def worker_input(worker_type: WorkerType) -> WorkerInput:
    worker = worker_type.value
    return WorkerInput(
        schema_version="router.v2",
        task_id="task-live-subagent-smoke",
        worker_job_id=f"worker-job-live-{worker}",
        worker_type=worker,
        mcp_tool=WORKER_TOOL_BY_TYPE[worker],
        mode={
            WorkerType.PLC_DEV: WorkerMode.CREATE,
            WorkerType.PLC_TEST: WorkerMode.TEST,
            WorkerType.PLC_FORMAL: WorkerMode.FORMAL_VERIFY,
            WorkerType.PLC_REPAIR: WorkerMode.REPAIR,
        }[worker_type],
        objective=objective(worker_type),
        workspace_root="/tmp/router-live-subagent-smoke",
        current_directory="/tmp/router-live-subagent-smoke",
        input_paths=[snapshot.path for snapshot in snapshots(worker_type)],
        output_paths=output_paths(worker_type),
        context=WorkerContext(
            user_goal="Build a safe motor start/stop controller with stop priority.",
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
        trace_context=TraceContext(worker_job_id=f"worker-job-live-{worker}"),
        idempotency_key=f"task-live-subagent-smoke:worker-job-live-{worker}",
        created_at=datetime.now(UTC),
        worker_config=worker_config(worker_type),
    )


def objective(worker_type: WorkerType) -> str:
    return {
        WorkerType.PLC_DEV: "Generate ST code for safe motor start/stop control.",
        WorkerType.PLC_TEST: "Run fuzz testing for the supplied ST motor control code.",
        WorkerType.PLC_FORMAL: "Formally verify the supplied ST motor control code.",
        WorkerType.PLC_REPAIR: "Repair the supplied ST motor control code based on failure evidence.",
    }[worker_type]


def worker_config(worker_type: WorkerType) -> dict[str, Any]:
    return {
        WorkerType.PLC_DEV: {
            "target_language": "ST",
            "compiler_type": "rusty",
            "rpc_pipeline": ["fuzz", "formal"],
            "enable_socratic_spec": False,
        },
        WorkerType.PLC_TEST: {
            "fuzz_method": "boundary",
            "case_count": 5,
            "enable_fuzz_test": True,
        },
        WorkerType.PLC_FORMAL: {
            "compiler_type": "matiec",
            "properties": formal_properties(),
        },
        WorkerType.PLC_REPAIR: {
            "repair_source": "test_failure",
            "repair_targets": ["test_failure"],
            "repair_failure_notes": "Fuzz case tc_023 found MotorRun can remain true after StopBtn.",
            "compiler_type": "rusty",
        },
    }[worker_type]


def snapshots(worker_type: WorkerType) -> list[McpInputFileSnapshot]:
    raw = McpInputFileSnapshot(
        path=".router/raw_request.txt",
        type=ArtifactType.RAW_USER_REQUEST,
        version=1,
        summary="Raw live smoke request.",
        content="Create a safe ST motor start/stop controller with stop priority.",
        content_chars=62,
        mime_type="text/plain",
    )
    requirements = McpInputFileSnapshot(
        path=".router/requirements.json",
        type=ArtifactType.REQUIREMENTS_IR,
        version=1,
        summary="Minimal requirements.",
        content=json.dumps(
            {
                "requirements": [
                    "StopBtn has priority over StartBtn.",
                    "MotorRun is false whenever StopBtn is true.",
                ]
            },
            ensure_ascii=False,
        ),
        content_chars=96,
        mime_type="application/json",
    )
    code = McpInputFileSnapshot(
        path="src/plc_code.st",
        type=ArtifactType.PLC_CODE,
        version=1,
        summary="Sample ST code.",
        content=(
            sample_formal_code()
            if worker_type == WorkerType.PLC_FORMAL
            else sample_code()
        ),
        content_chars=(
            len(sample_formal_code())
            if worker_type == WorkerType.PLC_FORMAL
            else len(sample_code())
        ),
        mime_type="text/plain",
    )
    report = McpInputFileSnapshot(
        path=".router/reports/test_report.json",
        type=ArtifactType.TEST_REPORT,
        version=1,
        summary="Synthetic failing fuzz report.",
        content=json.dumps(
            {
                "status": "failed",
                "failed_case": "tc_023",
                "actual": "MotorRun remained true after StopBtn.",
            },
            ensure_ascii=False,
        ),
        content_chars=92,
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
            ".router/reports/worker-job-live/requirements.json",
            ".router/reports/worker-job-live/io_contract.json",
        ],
        WorkerType.PLC_TEST: [".router/reports/worker-job-live/test_report.json"],
        WorkerType.PLC_FORMAL: [".router/reports/worker-job-live/formal_report.json"],
        WorkerType.PLC_REPAIR: [
            "src/plc_code.st",
            ".router/reports/worker-job-live/patch.diff",
            ".router/reports/worker-job-live/repair_summary.json",
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
    return [
        {
            "property_description": "输出 y 必须等于输入 x / y must equal x",
            "property": {"job_req": "assertion"},
        }
    ]


def sample_formal_code() -> str:
    return (
        "FUNCTION_BLOCK Example\n"
        "VAR_INPUT\n"
        "    x : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    y : BOOL;\n"
        "END_VAR\n"
        "y := x;\n"
        "//#ASSERT (y = x) : assert_y_equals_x\n"
        "END_FUNCTION_BLOCK"
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
