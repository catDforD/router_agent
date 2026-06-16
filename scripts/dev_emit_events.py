"""Append representative user-visible Router events for SSE development."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.database import session_scope  # noqa: E402
from app.core.errors import RepositoryNotFoundError  # noqa: E402
from app.models.router_schema import RouterEvent  # noqa: E402
from app.services.event_service import EventService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append sample user-visible events for an existing task.",
    )
    parser.add_argument("--task-id", required=True, help="Existing Router task ID.")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.5,
        help="Delay between emitted events.",
    )
    return parser.parse_args()


def build_event(
    *,
    task_id: str,
    event_type: str,
    title: str,
    message: str,
    worker_job_id: str,
    artifact_id: str | None = None,
) -> RouterEvent:
    run_id = uuid4().hex
    correlation: dict[str, object] = {"worker_job_id": worker_job_id}
    payload: dict[str, object] = {
        "worker_type": "plc-dev",
        "worker_job_id": worker_job_id,
        "summary": message,
    }
    if artifact_id is not None:
        correlation["artifact_ids"] = [artifact_id]
        payload["artifact_id"] = artifact_id

    return RouterEvent.model_validate(
        {
            "schema_version": "router.v1",
            "event_id": f"event-{run_id}",
            "task_id": task_id,
            "seq": 0,
            "type": event_type,
            "source": {
                "type": "worker",
                "worker_type": "plc-dev",
                "id": worker_job_id,
            },
            "severity": "info",
            "visibility": "user",
            "title": title,
            "message": message,
            "correlation": correlation,
            "payload": payload,
            "created_at": datetime.now(UTC),
        }
    )


def main() -> None:
    args = parse_args()
    worker_job_id = f"worker-job-dev-{uuid4().hex[:12]}"
    artifact_id = f"artifact-dev-{uuid4().hex[:12]}"
    events = [
        build_event(
            task_id=args.task_id,
            event_type="worker.started",
            title="PLC development worker started",
            message="The PLC development worker started generating code.",
            worker_job_id=worker_job_id,
        ),
        build_event(
            task_id=args.task_id,
            event_type="artifact.created",
            title="PLC code artifact created",
            message="A PLC code artifact was created by the worker.",
            worker_job_id=worker_job_id,
            artifact_id=artifact_id,
        ),
        build_event(
            task_id=args.task_id,
            event_type="worker.completed",
            title="PLC development worker completed",
            message="The PLC development worker completed successfully.",
            worker_job_id=worker_job_id,
            artifact_id=artifact_id,
        ),
    ]

    print(f"Open another shell and run:")
    print(f"curl -N http://localhost:8000/api/tasks/{args.task_id}/events")

    try:
        for event in events:
            with session_scope() as session:
                appended = EventService(session).append_event(event)
            print(f"emitted {appended.type} seq={appended.seq}")
            time.sleep(args.delay_seconds)
    except RepositoryNotFoundError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
