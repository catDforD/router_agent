"""Create representative local artifacts through the Artifact Store service."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.errors import RepositoryNotFoundError  # noqa: E402
from app.models.router_schema import ArtifactCreator, ArtifactCreatorType, TaskState  # noqa: E402
from app.repositories.task_repo import TaskRepository  # noqa: E402
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore  # noqa: E402


FIXTURE_DIR = ROOT / "backend" / "app" / "tests" / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def ensure_task(task_repo: TaskRepository) -> TaskState:
    task = TaskState.model_validate(load_fixture("task_state.valid.json"))
    try:
        task_repo.get_task(task.task_id)
        print(f"task already exists: {task.task_id}")
    except RepositoryNotFoundError:
        task_repo.create_task(task)
        print(f"created task: {task.task_id}")
    return task


def main() -> None:
    settings = get_settings()
    creator = ArtifactCreator(type=ArtifactCreatorType.RUNTIME)

    with session_scope() as session:
        task = ensure_task(TaskRepository(session))
        store = ArtifactStore(session=session, artifact_root=settings.artifact_root)
        writes = [
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type="requirements_ir",
                version=1,
                name="requirements_ir_v1.json",
                content={
                    "goal": task.normalized_goal or task.raw_user_request,
                    "requirements": ["Pump interlock prevents unsafe start"],
                },
                summary="Representative requirements IR for local artifact testing.",
                created_by=creator,
                mime_type="application/json",
            ),
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type="plc_code",
                version=1,
                name="pump_interlock.st",
                content=(
                    "FUNCTION_BLOCK FB_PumpInterlock\n"
                    "VAR_INPUT\n"
                    "    StartCmd : BOOL;\n"
                    "    FaultActive : BOOL;\n"
                    "END_VAR\n"
                    "VAR_OUTPUT\n"
                    "    PumpRun : BOOL;\n"
                    "END_VAR\n"
                    "PumpRun := StartCmd AND NOT FaultActive;\n"
                    "END_FUNCTION_BLOCK\n"
                ),
                summary="Representative Structured Text implementation.",
                parent_artifact_ids=(),
                created_by=creator,
                metadata={
                    "target_plc_language": "ST",
                    "target_platform": "Codesys",
                    "tags": ["dev-script", "plc"],
                },
                mime_type="text/plain",
            ),
        ]

        results = [store.write_artifact_content(write) for write in writes]

    for result in results:
        artifact = result.artifact
        print(
            "created artifact: "
            f"{artifact.artifact_id} uri={artifact.storage.uri} "
            f"hash={artifact.storage.content_hash}"
        )

    print()
    print("Example checks:")
    print(f"curl http://localhost:8000/api/tasks/{task.task_id}/artifacts")
    for result in results:
        print(f"curl http://localhost:8000/api/artifacts/{result.artifact.artifact_id}")


if __name__ == "__main__":
    main()
