"""Run a quick local sanity check for Scheduler Guard rejection paths."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.router_schema import ArtifactRef, CurrentArtifacts, Failure, TaskState  # noqa: E402
from app.services.scheduler_guard import (  # noqa: E402
    SchedulerGuardViolation,
    validate_finish_task,
    validate_worker_call,
)


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def artifact_ref(artifact_id: str, artifact_type: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        type=artifact_type,
        version=1,
        uri=f"local://artifacts/task-001/{artifact_id}",
    )


def running_task(
    *,
    current_code: ArtifactRef | None = None,
    requirements: ArtifactRef | None = None,
) -> TaskState:
    base = TaskState.model_validate(load_fixture("task_state.valid.json"))
    raw = artifact_ref("artifact-raw-request-001", "raw_user_request")
    return base.model_copy(
        deep=True,
        update={
            "status": "running",
            "phase": "planning",
            "task_type": "new_plc_development",
            "current_artifacts": CurrentArtifacts(
                raw_user_request=raw,
                requirements_ir=requirements,
                current_code=current_code,
                all_artifact_ids=[
                    artifact.artifact_id
                    for artifact in (raw, requirements, current_code)
                    if artifact is not None
                ],
            ),
        },
    )


def blocking_failure(state: TaskState) -> Failure:
    return Failure(
        failure_id="failure-blocking-001",
        source="test",
        severity="blocking",
        title="Blocking test failure",
        description="The generated code violates a blocking test.",
        evidence_artifact_ids=["artifact-test-report-001"],
        status="open",
        created_at=state.created_at,
    )


def expect_rejected(label: str, action: Callable[[], None]) -> None:
    try:
        action()
    except SchedulerGuardViolation:
        print(f"PASS: {label}")
        return
    raise SystemExit(f"FAIL: {label}")


def main() -> None:
    requirements = artifact_ref("artifact-requirements-001", "requirements_ir")
    code = artifact_ref("artifact-code-001", "plc_code")
    test_report = artifact_ref("artifact-test-report-001", "test_report")

    expect_rejected(
        "test without code rejected",
        lambda: validate_worker_call(
            running_task(requirements=requirements),
            "plc-test",
            [requirements],
        ),
    )
    expect_rejected(
        "repair without failure rejected",
        lambda: validate_worker_call(
            running_task(current_code=code, requirements=requirements),
            "plc-repair",
            [code, test_report],
        ),
    )

    blocked = running_task()
    blocked = blocked.model_copy(
        deep=True,
        update={
            "failures": [blocking_failure(blocked)],
            "gates": blocked.gates.model_copy(update={"has_blocking_failure": True}),
        },
    )
    expect_rejected(
        "finish with blocking failure rejected",
        lambda: validate_finish_task(blocked, "succeeded"),
    )


if __name__ == "__main__":
    main()
