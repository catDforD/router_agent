"""Opt-in live smoke call for one LLM-backed PLC MCP worker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.ids import new_session_id, new_task_id  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.mcp.adapter import McpAdapter  # noqa: E402
from app.models.router_schema import (  # noqa: E402
    ArtifactType,
    CurrentArtifacts,
    TaskPhase,
    TaskState,
    TaskStatus,
    WorkerType,
)
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore  # noqa: E402
from app.workers.worker_input_builder import build_worker_input  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call one real PLC MCP worker through Router. Requires --live.",
    )
    parser.add_argument(
        "--worker",
        required=True,
        choices=[worker.value for worker in WorkerType],
        help="Worker type to invoke.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually call the configured MCP server and DeepSeek-backed worker.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if not args.live:
        raise SystemExit("Refusing live MCP/DeepSeek call without --live.")
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required for live worker simulation.")

    with session_scope() as session:
        task = create_task(session)
        task = prepare_inputs(session, settings.artifact_root, task, args.worker)
        worker_input = build_worker_input(task, args.worker)
        result = McpAdapter(
            session=session,
            artifact_root=settings.artifact_root,
            mcp_mode="real",
        ).call_worker(worker_input)
        session.commit()

    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


def create_task(session) -> TaskState:
    base = TaskState.model_validate(
        json.loads((FIXTURE_DIR / "task_state.valid.json").read_text(encoding="utf-8"))
    )
    now = utc_now()
    task = base.model_copy(
        deep=True,
        update={
            "task_id": new_task_id(),
            "session_id": new_session_id(),
            "status": TaskStatus.RUNNING,
            "phase": TaskPhase.PLANNING,
            "normalized_goal": base.raw_user_request,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "event_seq": 0,
            "current_artifacts": CurrentArtifacts(all_artifact_ids=[]),
            "active_worker_jobs": [],
            "completed_worker_job_ids": [],
            "failures": [],
        },
    )
    TaskRepository(session).create_task(task)
    return task


def prepare_inputs(
    session,
    artifact_root: Path,
    task: TaskState,
    worker_type: str,
) -> TaskState:
    store = ArtifactStore(session, artifact_root)
    if worker_type == WorkerType.PLC_DEV.value:
        write(store, task, ArtifactType.RAW_USER_REQUEST, task.raw_user_request, "raw_request.txt")
    elif worker_type in {WorkerType.PLC_TEST.value, WorkerType.PLC_FORMAL.value}:
        write(store, task, ArtifactType.REQUIREMENTS_IR, {"requirements": []}, "requirements.json")
        write(store, task, ArtifactType.PLC_CODE, sample_code(), "plc_code_v1.st")
    elif worker_type == WorkerType.PLC_REPAIR.value:
        write(store, task, ArtifactType.PLC_CODE, sample_code(), "plc_code_v1.st")
        write(
            store,
            task,
            ArtifactType.TEST_REPORT,
            {"status": "failed", "failed_case": "emergency_stop"},
            "test_report_failed.json",
        )
    return TaskRepository(session).get_task(task.task_id)


def write(
    store: ArtifactStore,
    task: TaskState,
    artifact_type: ArtifactType,
    content,
    name: str,
) -> None:
    store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task.task_id,
            artifact_type=artifact_type,
            version=1,
            name=name,
            content=content,
            summary=f"Live smoke {artifact_type.value} input.",
            mime_type="application/json" if isinstance(content, dict) else "text/plain",
        )
    )


def sample_code() -> str:
    return (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartBtn : BOOL;\n"
        "    StopBtn : BOOL;\n"
        "    EmergencyStop : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "END_VAR\n"
        "IF StopBtn OR EmergencyStop THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartBtn THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "END_FUNCTION_BLOCK\n"
    )


if __name__ == "__main__":
    main()
