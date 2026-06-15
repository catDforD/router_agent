import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import (
    ArtifactStoreConflictError,
    ArtifactStoreInvalidStorageError,
    RepositoryConflictError,
)
from app.models.db_models import Base
from app.models.router_schema import (
    Artifact,
    ArtifactStorageProvider,
    ArtifactType,
    TaskState,
)
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def task(db_session: Session) -> TaskState:
    task_state = TaskState.model_validate(load_fixture("task_state.valid.json"))
    TaskRepository(db_session).create_task(task_state)
    return task_state


@pytest.fixture()
def store(db_session: Session, tmp_path: Path, task: TaskState) -> ArtifactStore:
    return ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def write_request(
    task_id: str,
    *,
    artifact_id: str | None = None,
    artifact_type: ArtifactType | str = ArtifactType.PLC_CODE,
    version: int = 1,
    name: str = "pump_interlock.st",
    content: str = "PumpRun := StartCmd AND NOT FaultActive;\n",
    created_at: datetime | None = None,
) -> ArtifactContentWrite:
    return ArtifactContentWrite(
        task_id=task_id,
        artifact_type=artifact_type,
        version=version,
        name=name,
        content=content,
        summary=f"{artifact_type} artifact",
        metadata={"tags": ["unit"]},
        artifact_id=artifact_id,
        created_at=created_at,
    )


def test_write_then_read_artifact_content_and_metadata(
    store: ArtifactStore,
    task: TaskState,
) -> None:
    content = "PumpRun := StartCmd AND NOT FaultActive;\n"

    result = store.write_artifact_content(
        write_request(task.task_id, content=content, name="../pump interlock.st")
    )
    stored = store.read_artifact_content(result.artifact.artifact_id)

    assert stored.content == content.encode("utf-8")
    assert stored.artifact == result.artifact
    assert result.artifact.storage.provider == ArtifactStorageProvider.LOCAL.value
    assert result.artifact.storage.uri.startswith("local://artifacts/")
    assert result.artifact.storage.path is not None
    assert result.artifact.storage.path.endswith("pump_interlock.st")
    assert result.artifact.storage.size_bytes == len(content.encode("utf-8"))
    assert result.artifact.storage.content_hash == (
        f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    )
    assert result.artifact.inline_content is None
    assert result.content_path.read_bytes() == content.encode("utf-8")


def test_same_type_new_version_does_not_overwrite_previous_content(
    store: ArtifactStore,
    task: TaskState,
) -> None:
    first = store.write_artifact_content(
        write_request(
            task.task_id,
            artifact_id="artifact-code-v1",
            version=1,
            content="version one\n",
        )
    )
    second = store.write_artifact_content(
        write_request(
            task.task_id,
            artifact_id="artifact-code-v2",
            version=2,
            content="version two\n",
        )
    )

    assert store.read_artifact_content(first.artifact.artifact_id).content == b"version one\n"
    assert store.read_artifact_content(second.artifact.artifact_id).content == b"version two\n"
    assert first.content_path != second.content_path
    assert first.content_path.exists()
    assert second.content_path.exists()


def test_duplicate_artifact_id_is_rejected_and_new_file_is_cleaned_up(
    store: ArtifactStore,
    task: TaskState,
) -> None:
    store.write_artifact_content(
        write_request(task.task_id, artifact_id="artifact-duplicate", version=1)
    )

    with pytest.raises(RepositoryConflictError):
        store.write_artifact_content(
            write_request(
                task.task_id,
                artifact_id="artifact-duplicate",
                version=2,
                name="other.st",
            )
        )

    v2_files = list((store.artifact_root / task.task_id / "plc_code" / "v2").glob("*"))
    assert v2_files == []


def test_existing_final_path_is_rejected(
    store: ArtifactStore,
    task: TaskState,
) -> None:
    relative_path = store._build_relative_path(
        task_id=task.task_id,
        artifact_type=ArtifactType.PLC_CODE,
        version=1,
        artifact_id="artifact-path-exists",
        safe_name="existing.st",
    )
    content_path = store._resolve_local_path(relative_path)
    content_path.parent.mkdir(parents=True)
    content_path.write_text("existing", encoding="utf-8")

    with pytest.raises(ArtifactStoreConflictError):
        store.write_artifact_content(
            write_request(
                task.task_id,
                artifact_id="artifact-path-exists",
                name="existing.st",
            )
        )

    assert content_path.read_text(encoding="utf-8") == "existing"


def test_local_path_traversal_is_rejected(
    db_session: Session,
    store: ArtifactStore,
    task: TaskState,
) -> None:
    valid = store.write_artifact_content(
        write_request(task.task_id, artifact_id="artifact-valid-path")
    ).artifact
    escaped = valid.model_copy(
        update={
            "artifact_id": "artifact-escaped-path",
            "storage": valid.storage.model_copy(
                update={
                    "path": "../escape.txt",
                    "uri": "local://artifacts/../escape.txt",
                }
            ),
        }
    )
    ArtifactRepository(db_session).create_artifact(escaped)

    with pytest.raises(ArtifactStoreInvalidStorageError):
        store.read_artifact_content(escaped.artifact_id)


def test_list_task_artifacts_and_get_artifact_ref(
    store: ArtifactStore,
    task: TaskState,
) -> None:
    first_time = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    second_time = first_time + timedelta(minutes=1)
    first = store.write_artifact_content(
        write_request(
            task.task_id,
            artifact_id="artifact-list-001",
            version=1,
            created_at=first_time,
        )
    ).artifact
    second = store.write_artifact_content(
        write_request(
            task.task_id,
            artifact_id="artifact-list-002",
            version=2,
            created_at=second_time,
        )
    ).artifact

    listed = store.list_task_artifacts(task.task_id)
    artifact_ref = store.get_artifact_ref(first.artifact_id)

    assert [artifact.artifact_id for artifact in listed] == [
        first.artifact_id,
        second.artifact_id,
    ]
    assert artifact_ref.artifact_id == first.artifact_id
    assert artifact_ref.type == first.type
    assert artifact_ref.version == first.version
    assert artifact_ref.uri == first.storage.uri
    assert artifact_ref.content_hash == first.storage.content_hash


def test_task_state_current_artifacts_are_updated(
    db_session: Session,
    store: ArtifactStore,
    task: TaskState,
) -> None:
    code = store.write_artifact_content(
        write_request(task.task_id, artifact_id="artifact-current-code")
    ).artifact
    log = store.write_artifact_content(
        write_request(
            task.task_id,
            artifact_id="artifact-worker-log",
            artifact_type=ArtifactType.WORKER_LOG,
            name="worker.log",
            content="worker log\n",
        )
    ).artifact

    updated_task = TaskRepository(db_session).get_task(task.task_id)

    assert updated_task.current_artifacts.current_code is not None
    assert updated_task.current_artifacts.current_code.artifact_id == code.artifact_id
    assert code.artifact_id in updated_task.current_artifacts.all_artifact_ids
    assert log.artifact_id in updated_task.current_artifacts.all_artifact_ids
