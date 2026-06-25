"""Submit realistic PLC agent smoke tasks through the running HTTP API."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_WORKSPACE_ROOT = ROOT / "data" / "api-smoke-workspaces"
TERMINAL_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}
DONE_STATUSES = TERMINAL_STATUSES | {"waiting_user"}


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    title: str
    message: str
    files: dict[str, str] = field(default_factory=dict)
    expected_statuses: tuple[str, ...] = ("succeeded",)
    expected_events: tuple[str, ...] = (
        "agent.started",
        "agent.tool_called",
        "agent.tool_result",
        "agent.completed",
    )


def main() -> int:
    args = parse_args()
    selected = select_cases(args.case)
    if args.list_cases:
        for case in CASES:
            print(f"{case.case_id}: {case.title}")
        return 0

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    workspace_base = args.workspace_root.resolve() / run_id
    prepared = [
        (case, prepare_workspace(case, workspace_base / case.case_id))
        for case in selected
    ]

    if args.prepare_only:
        for case, workspace in prepared:
            print_payload(case, workspace, args.base_url)
        return 0

    with httpx.Client(base_url=args.base_url, timeout=args.http_timeout) as client:
        check_health(client)
        failures = []
        for case, workspace in prepared:
            result = run_case(
                client=client,
                case=case,
                workspace=workspace,
                poll_interval=args.poll_interval,
                timeout_seconds=args.timeout,
                show_final_report=args.show_final_report,
            )
            print_result(result)
            if args.strict:
                failures.extend(validate_result(case, result))

    if failures:
        print("\nFailures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create realistic PLC workspaces and submit agent tasks through "
            "the running Router backend API."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Directory where per-case workspaces are created.",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=[case.case_id for case in CASES] + ["all"],
        default=None,
        help="Case to run. Repeat for multiple cases. Defaults to all.",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--http-timeout", type=float, default=30.0)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--show-final-report", action="store_true")
    parser.add_argument(
        "--no-strict",
        action="store_false",
        dest="strict",
        help="Do not return non-zero when expected status/events are missing.",
    )
    parser.set_defaults(strict=True)
    return parser.parse_args()


def select_cases(case_ids: list[str] | None) -> list[SmokeCase]:
    if not case_ids or "all" in case_ids:
        return list(CASES)
    by_id = {case.case_id: case for case in CASES}
    return [by_id[case_id] for case_id in case_ids]


def prepare_workspace(case: SmokeCase, workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    for relative_path, content in case.files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def check_health(client: httpx.Client) -> None:
    response = client.get("/api/health")
    response.raise_for_status()


def run_case(
    *,
    client: httpx.Client,
    case: SmokeCase,
    workspace: Path,
    poll_interval: float,
    timeout_seconds: float,
    show_final_report: bool,
) -> dict[str, Any]:
    payload = task_payload(case, workspace)
    create_response = client.post("/api/tasks", json=payload)
    create_response.raise_for_status()
    created = create_response.json()
    task_id = created["task_id"]

    deadline = time.monotonic() + timeout_seconds
    task = None
    while time.monotonic() < deadline:
        task_response = client.get(f"/api/tasks/{task_id}")
        task_response.raise_for_status()
        task = task_response.json()
        if task["status"] in DONE_STATUSES:
            break
        time.sleep(poll_interval)

    if task is None:
        raise RuntimeError(f"task was not readable after creation: {task_id}")

    trace = get_json(client, f"/api/tasks/{task_id}/trace")
    artifact_index = get_json(client, f"/api/tasks/{task_id}/artifacts")
    final_report = None
    if show_final_report:
        final_report = read_first_artifact_content(
            client,
            artifact_index,
            artifact_type="final_report",
        )

    return {
        "case_id": case.case_id,
        "title": case.title,
        "task_id": task_id,
        "status": task["status"],
        "phase": task["phase"],
        "events_url": created["events_url"],
        "workspace": str(workspace),
        "trace": trace,
        "artifacts": artifact_index.get("artifacts", []),
        "final_report": final_report,
    }


def get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def read_first_artifact_content(
    client: httpx.Client,
    artifact_index: dict[str, Any],
    *,
    artifact_type: str,
) -> dict[str, Any] | None:
    for artifact in artifact_index.get("artifacts", []):
        if artifact.get("type") != artifact_type:
            continue
        return get_json(client, f"/api/artifacts/{artifact['artifact_id']}")
    return None


def task_payload(case: SmokeCase, workspace: Path) -> dict[str, Any]:
    return {
        "message": case.message,
        "project_context": {
            "target_plc_language": "ST",
            "target_platform": "Codesys",
            "workspace_root": str(workspace),
        },
    }


def print_payload(case: SmokeCase, workspace: Path, base_url: str) -> None:
    print(f"\n# {case.case_id}: {case.title}")
    print(f"workspace={workspace}")
    print(f"POST {base_url}/api/tasks")
    print(json.dumps(task_payload(case, workspace), indent=2, ensure_ascii=False))


def print_result(result: dict[str, Any]) -> None:
    trace = result["trace"]
    events = [event["type"] for event in trace.get("events", [])]
    artifact_types = [artifact["type"] for artifact in result["artifacts"]]
    worker_types = [job["worker_type"] for job in trace.get("worker_jobs", [])]

    print(f"\n== {result['case_id']}: {result['title']} ==")
    print(f"task_id: {result['task_id']}")
    print(f"status: {result['status']} / phase: {result['phase']}")
    print(f"events_url: {result['events_url']}")
    print(f"workspace: {result['workspace']}")
    print(f"events: {events}")
    print(f"workers: {worker_types}")
    print(f"artifacts: {artifact_types}")
    if result["final_report"] is not None:
        print("\nfinal_report:")
        print(result["final_report"]["content"])


def validate_result(case: SmokeCase, result: dict[str, Any]) -> list[str]:
    failures = []
    if result["status"] not in case.expected_statuses:
        failures.append(
            f"{case.case_id}: expected status in {case.expected_statuses}, "
            f"got {result['status']}"
        )
    event_types = [event["type"] for event in result["trace"].get("events", [])]
    for expected_event in case.expected_events:
        if expected_event not in event_types:
            failures.append(f"{case.case_id}: missing event {expected_event}")
    return failures


COMMON_INSTRUCTIONS = """
Use the configured workspace. Inspect README.md and any existing files first.
Make the smallest useful PLC changes. Prefer src/*.st files. Run the validation
command mentioned in the workspace before finishing. Finish with succeeded only
when validation passes; otherwise finish failed or partial_failed with a clear
summary.
""".strip()


CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        case_id="local_motor_start_stop",
        title="Create motor start/stop ST logic in a workspace",
        files={
            "README.md": """
# PLC Task: motor start/stop

Create `src/motor_start_stop.st`.

Inputs:
- StartPB
- StopPB
- EStop

Outputs/state:
- MotorRun
- RunLamp

Safety contract:
- EStop must immediately force MotorRun false.
- StopPB must stop the motor.
- StartPB may latch MotorRun only when StopPB and EStop are false.
- RunLamp must mirror MotorRun.

Validation:

```bash
python tests/check_contract.py
```
""".lstrip(),
            "tests/check_contract.py": """
from pathlib import Path

code_path = Path("src/motor_start_stop.st")
assert code_path.exists(), "src/motor_start_stop.st was not created"
text = code_path.read_text(encoding="utf-8")
lower = text.lower()
for token in ["startpb", "stoppb", "estop", "motorrun", "runlamp"]:
    assert token in lower, f"missing {token}"
assert "motorrun := false" in lower, "MotorRun must have an explicit false path"
assert "estop" in lower and "not estop" in lower, "EStop must gate start/run logic"
assert "runlamp := motorrun" in lower, "RunLamp must mirror MotorRun"
print("contract ok")
""".lstrip(),
        },
        message=(
            "Implement a Codesys ST motor start/stop controller. "
            f"{COMMON_INSTRUCTIONS}"
        ),
    ),
    SmokeCase(
        case_id="debug_estop_latch",
        title="Debug existing ST logic missing E-stop handling",
        files={
            "README.md": """
# PLC Debug Task: E-stop latch bug

The existing `src/motor_control.st` lets the motor remain latched when EStop is
pressed. Repair the code.

Validation:

```bash
python tests/check_contract.py
```
""".lstrip(),
            "src/motor_control.st": """
FUNCTION_BLOCK FB_MotorControl
VAR_INPUT
    StartPB : BOOL;
    StopPB : BOOL;
    EStop : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
    RunLamp : BOOL;
END_VAR

IF StartPB THEN
    MotorRun := TRUE;
END_IF;

IF StopPB THEN
    MotorRun := FALSE;
END_IF;

RunLamp := MotorRun;
END_FUNCTION_BLOCK
""".lstrip(),
            "tests/check_contract.py": """
from pathlib import Path

text = Path("src/motor_control.st").read_text(encoding="utf-8").lower()
assert "estop" in text, "EStop input must still exist"
assert "motorrun := false" in text, "MotorRun must be forced false"
assert "estop" in text and "motorrun := false" in text, "EStop must force stop"
assert "not estop" in text, "Start/latch path must be gated by NOT EStop"
assert "runlamp := motorrun" in text, "RunLamp must mirror MotorRun"
print("contract ok")
""".lstrip(),
        },
        message=(
            "Fix the E-stop safety bug in the existing ST function block. "
            f"{COMMON_INSTRUCTIONS}"
        ),
    ),
    SmokeCase(
        case_id="debug_timer_fault_reset",
        title="Repair timer duration and fault reset behavior",
        files={
            "README.md": """
# PLC Debug Task: pump fault timer

Repair `src/pump_fault.st`.

Required behavior:
- FaultTimer preset must be T#10S.
- FaultLatch must set after FaultTimer.Q.
- ResetPB must clear FaultLatch.
- PumpRun must be false while FaultLatch or EStop is true.

Validation:

```bash
python tests/check_contract.py
```
""".lstrip(),
            "src/pump_fault.st": """
FUNCTION_BLOCK FB_PumpFault
VAR_INPUT
    StartPB : BOOL;
    StopPB : BOOL;
    ResetPB : BOOL;
    EStop : BOOL;
    Overload : BOOL;
END_VAR
VAR_OUTPUT
    PumpRun : BOOL;
    FaultLatch : BOOL;
END_VAR
VAR
    FaultTimer : TON;
END_VAR

FaultTimer(IN := Overload, PT := T#5S);

IF FaultTimer.Q THEN
    FaultLatch := TRUE;
END_IF;

IF StartPB AND NOT StopPB THEN
    PumpRun := TRUE;
END_IF;

IF StopPB OR EStop THEN
    PumpRun := FALSE;
END_IF;
END_FUNCTION_BLOCK
""".lstrip(),
            "tests/check_contract.py": """
from pathlib import Path

text = Path("src/pump_fault.st").read_text(encoding="utf-8").lower().replace(" ", "")
assert "pt:=t#10s" in text, "FaultTimer preset must be T#10S"
assert "faulttimer.q" in text and "faultlatch:=true" in text, "timer must set FaultLatch"
assert "resetpb" in text and "faultlatch:=false" in text, "ResetPB must clear FaultLatch"
assert "faultlatch" in text and "pumprun:=false" in text, "FaultLatch must stop PumpRun"
assert "estop" in text and "pumprun:=false" in text, "EStop must stop PumpRun"
print("contract ok")
""".lstrip(),
        },
        message=(
            "Repair the pump fault timer and reset logic in the existing ST file. "
            f"{COMMON_INSTRUCTIONS}"
        ),
    ),
    SmokeCase(
        case_id="mcp_worker_dev_test",
        title="Exercise PLC domain workers through direct tools",
        files={
            "README.md": """
# MCP domain tool smoke

This case is intended to exercise `plc_dev` and `plc_test` rather than local
file edits. Use the PLC domain tools if available.
""".lstrip(),
        },
        message=(
            "This is a backend smoke test for domain tools. "
            "Call plc_dev to generate a simple ST conveyor start/stop "
            "implementation, then call plc_test to validate it. Finish the task "
            "with a summary of the worker results."
        ),
        expected_events=(
            "agent.started",
            "agent.tool_called",
            "agent.tool_result",
            "worker.started",
            "worker.completed",
            "agent.completed",
        ),
    ),
)


if __name__ == "__main__":
    raise SystemExit(main())
