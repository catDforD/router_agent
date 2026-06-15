import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.models.db_models import Base
from app.models.router_schema import TaskState
from app.repositories.artifact_repo import ArtifactRepository
from app.repositories.task_repo import TaskRepository
from app.main import create_app
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def api_context(tmp_path: Path) -> tuple[Settings, sessionmaker]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router.db'}"
    engine = get_engine_for_url(database_url)
    Base.metadata.create_all(engine)
    session_factory = get_session_factory_for_url(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        yield settings, session_factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_engine_for_url.cache_clear()
        get_session_factory_for_url.cache_clear()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def create_task(session_factory: sessionmaker) -> TaskState:
    task = TaskState.model_validate(load_fixture("task_state.valid.json"))
    with session_factory() as session:
        TaskRepository(session).create_task(task)
        session.commit()
    return task


def create_artifact(
    settings: Settings,
    session_factory: sessionmaker,
    task: TaskState,
    *,
    artifact_id: str = "artifact-api-code",
) -> str:
    with session_factory() as session:
        store = ArtifactStore(session=session, artifact_root=settings.artifact_root)
        result = store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type="plc_code",
                version=1,
                name="pump_interlock.st",
                content="PumpRun := StartCmd AND NOT FaultActive;\n",
                summary="API test PLC code artifact.",
                metadata={"tags": ["api"]},
                artifact_id=artifact_id,
                mime_type="text/plain",
            )
        )
        session.commit()
        return result.artifact.artifact_id


def test_task_artifact_list_endpoint_returns_metadata(
    api_context: tuple[Settings, sessionmaker],
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    artifact_id = create_artifact(settings, session_factory, task)

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/tasks/{task.task_id}/artifacts")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task.task_id
    assert [artifact["artifact_id"] for artifact in payload["artifacts"]] == [
        artifact_id
    ]
    assert "content" not in payload["artifacts"][0]


def test_artifact_content_endpoint_returns_text_content(
    api_context: tuple[Settings, sessionmaker],
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    artifact_id = create_artifact(settings, session_factory, task)

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/artifacts/{artifact_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact"]["artifact_id"] == artifact_id
    assert payload["content"] == "PumpRun := StartCmd AND NOT FaultActive;\n"
    assert payload["content_encoding"] == "utf-8"
    assert payload["mime_type"] == "text/plain"
    assert payload["size_bytes"] == len(payload["content"].encode("utf-8"))
    assert payload["content_hash"].startswith("sha256:")


def test_artifact_api_missing_records_return_not_found(
    api_context: tuple[Settings, sessionmaker],
) -> None:
    settings, _session_factory = api_context

    with TestClient(create_app(settings)) as client:
        missing_artifact = client.get("/api/artifacts/missing")
        missing_task = client.get("/api/tasks/missing/artifacts")

    assert missing_artifact.status_code == 404
    assert missing_task.status_code == 404


def test_artifact_content_endpoint_reports_invalid_storage(
    api_context: tuple[Settings, sessionmaker],
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    artifact_id = create_artifact(
        settings,
        session_factory,
        task,
        artifact_id="artifact-valid-storage",
    )

    with session_factory() as session:
        artifact = ArtifactRepository(session).get_artifact(artifact_id)
        escaped = artifact.model_copy(
            update={
                "artifact_id": "artifact-invalid-storage",
                "storage": artifact.storage.model_copy(
                    update={
                        "path": "../escape.txt",
                        "uri": "local://artifacts/../escape.txt",
                    }
                ),
            }
        )
        ArtifactRepository(session).create_artifact(escaped)
        session.commit()

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/artifacts/artifact-invalid-storage")

    assert response.status_code == 409
    assert "escapes artifact root" in response.json()["detail"]
