"""Seed representative Router runtime records for local database inspection."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.database import session_scope  # noqa: E402
from app.core.errors import RepositoryConflictError, RepositoryNotFoundError  # noqa: E402
from app.models.router_schema import Artifact, RouterEvent, TaskState, WorkerInput  # noqa: E402
from app.models.router_schema import WorkerResult  # noqa: E402
from app.repositories.artifact_repo import ArtifactRepository  # noqa: E402
from app.repositories.event_repo import EventRepository  # noqa: E402
from app.repositories.gate_repo import GateResultRepository  # noqa: E402
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.repositories.worker_job_repo import WorkerJobRepository  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def worker_result_for_dev_job() -> WorkerResult:
    payload = load_fixture("worker_result.test_failed.valid.json")
    payload["worker_job_id"] = "worker-job-dev-001"
    payload["worker_type"] = "plc-dev"
    payload["mcp_tool"] = "plc_dev.run"
    payload["trace_context"]["worker_job_id"] = "worker-job-dev-001"
    return WorkerResult.model_validate(payload)


def main() -> None:
    task = TaskState.model_validate(load_fixture("task_state.valid.json"))
    event = RouterEvent.model_validate(load_fixture("event.worker_started.valid.json"))
    artifact = Artifact.model_validate(load_fixture("artifact.plc_code.valid.json"))
    worker_input = WorkerInput.model_validate(load_fixture("worker_input.plc_dev.valid.json"))
    worker_result = worker_result_for_dev_job()

    with session_scope() as session:
        task_repo = TaskRepository(session)
        event_repo = EventRepository(session)
        artifact_repo = ArtifactRepository(session)
        worker_job_repo = WorkerJobRepository(session)
        gate_repo = GateResultRepository(session)

        try:
            task_repo.get_task(task.task_id)
            print(f"task already exists: {task.task_id}")
        except RepositoryNotFoundError:
            task_repo.create_task(task)
            print(f"created task: {task.task_id}")

        if not event_repo.list_events(task.task_id):
            appended_event = event_repo.append_event(event)
            print(f"created event: {appended_event.event_id} seq={appended_event.seq}")
        else:
            print(f"events already exist for task: {task.task_id}")

        try:
            artifact_repo.get_artifact(artifact.artifact_id)
            print(f"artifact already exists: {artifact.artifact_id}")
        except RepositoryNotFoundError:
            artifact_repo.create_artifact(artifact)
            print(f"created artifact: {artifact.artifact_id}")

        try:
            worker_job_repo.get_job(worker_input.worker_job_id)
            print(f"worker job already exists: {worker_input.worker_job_id}")
        except RepositoryNotFoundError:
            worker_job_repo.create_job(worker_input)
            worker_job_repo.complete_job(worker_input.worker_job_id, worker_result)
            print(f"created worker job: {worker_input.worker_job_id}")

        if not gate_repo.list_results(task.task_id):
            gate_result = gate_repo.create_result(
                task_id=task.task_id,
                gate_type="quality_gate",
                status="failed",
                blocking=True,
                evidence_artifact_ids=["artifact-test-report-001"],
                result={"reason": "seeded gate result for local inspection"},
                created_at=task.updated_at,
                gate_result_id="gate-result-001",
            )
            print(f"created gate result: {gate_result.id}")
        else:
            print(f"gate results already exist for task: {task.task_id}")

    print("Seed complete.")


if __name__ == "__main__":
    try:
        main()
    except RepositoryConflictError as exc:
        raise SystemExit(f"seed conflict: {exc}") from exc
