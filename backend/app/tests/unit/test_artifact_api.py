import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.models.db_models import Base
from app.models.router_schema import ArtifactCreatorType, ArtifactVisibility, TaskState
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


def create_main_agent_artifacts(
    settings: Settings,
    session_factory: sessionmaker,
    task: TaskState,
) -> tuple[str, str, str]:
    final_report_content = {
        "kind": "main_agent_final_report",
        "schema_version": "router.v1",
        "report_version": 1,
        "task_id": task.task_id,
        "main_agent_run_id": "main-agent-run-001",
        "final_task_status": "succeeded",
        "user_goal": {"raw_user_request": task.raw_user_request},
        "classification": {
            "task_type": "new_plc_development",
            "difficulty": {"level": "L2"},
        },
        "delivery_artifacts": {
            "final_plc_code": {"artifact_id": "artifact-code-api"},
            "test_report": {"artifact_id": "artifact-test-report-api"},
            "all": [
                {"artifact_id": "artifact-code-api"},
                {"artifact_id": "artifact-test-report-api"},
            ],
        },
        "validation_summary": {"latest_test_passed": True},
        "repair_summary": {"repair_rounds": 0},
        "assumptions": [],
        "unresolved_items": {"blocking_failure_count": 0},
        "gate_summary": {},
        "trace_refs": {"main_agent_run_ids": ["main-agent-run-001"]},
        "summary": "Main Agent completed the PLC delivery.",
        "main_agent_output_summary": {
            "final_task_status": "succeeded",
            "summary": "Main Agent completed the PLC delivery.",
        },
    }
    replay_log_content = {
        "kind": "main_agent_replay_log",
        "entries": [
            {
                "type": "tool_result",
                "payload": {
                    "summary": "x" * 16_000,
                    "artifact_ids": ["artifact-final-report-api"],
                },
            }
        ],
    }
    with session_factory() as session:
        store = ArtifactStore(session=session, artifact_root=settings.artifact_root)
        final_report = store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type="final_report",
                version=1,
                name="main_agent_final_report.json",
                content=final_report_content,
                summary="Main Agent final report.",
                artifact_id="artifact-final-report-api",
                visibility=ArtifactVisibility.USER,
                created_by={
                    "type": ArtifactCreatorType.MAIN_AGENT,
                    "id": "main-agent-run-001",
                    "main_agent_run_id": "main-agent-run-001",
                },
                mime_type="application/json",
            )
        ).artifact
        replay_log = store.write_artifact_content(
            ArtifactContentWrite(
                task_id=task.task_id,
                artifact_type="main_agent_log",
                version=1,
                name="main_agent_replay_log.json",
                content=replay_log_content,
                summary="Main Agent replay log.",
                artifact_id="artifact-main-agent-log-api",
                visibility=ArtifactVisibility.INTERNAL,
                created_by={
                    "type": ArtifactCreatorType.MAIN_AGENT,
                    "id": "main-agent-run-001",
                    "main_agent_run_id": "main-agent-run-001",
                },
                mime_type="application/json",
            )
        ).artifact
        restored = TaskRepository(session).get_task(task.task_id)
        session.commit()
        return (
            final_report.artifact_id,
            replay_log.artifact_id,
            restored.current_artifacts.final_report.artifact_id,
        )


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


def test_final_report_artifact_is_readable_and_replay_log_stays_artifact_backed(
    api_context: tuple[Settings, sessionmaker],
) -> None:
    settings, session_factory = api_context
    task = create_task(session_factory)
    final_report_id, replay_log_id, current_final_report_id = create_main_agent_artifacts(
        settings,
        session_factory,
        task,
    )

    with TestClient(create_app(settings)) as client:
        list_response = client.get(f"/api/tasks/{task.task_id}/artifacts")
        report_response = client.get(f"/api/artifacts/{final_report_id}")
        log_response = client.get(f"/api/artifacts/{replay_log_id}")

    assert current_final_report_id == final_report_id
    assert list_response.status_code == 200
    artifacts = list_response.json()["artifacts"]
    assert [artifact["artifact_id"] for artifact in artifacts] == [
        final_report_id,
        replay_log_id,
    ]
    assert artifacts[0]["type"] == "final_report"
    assert artifacts[0]["visibility"] == "user"
    assert artifacts[1]["type"] == "main_agent_log"
    assert artifacts[1]["visibility"] == "internal"
    assert "content" not in artifacts[0]
    assert "x" * 16_000 not in json.dumps(artifacts)

    assert report_response.status_code == 200
    report_payload = report_response.json()
    report_content = json.loads(report_payload["content"])
    assert report_payload["artifact"]["type"] == "final_report"
    assert report_payload["artifact"]["visibility"] == "user"
    assert report_content["schema_version"] == "router.v1"
    assert report_content["report_version"] == 1
    assert report_content["summary"] == "Main Agent completed the PLC delivery."
    assert report_content["delivery_artifacts"]["final_plc_code"]["artifact_id"] == (
        "artifact-code-api"
    )
    assert report_content["unresolved_items"]["blocking_failure_count"] == 0

    assert log_response.status_code == 200
    log_content = json.loads(log_response.json()["content"])
    assert log_content["entries"][0]["payload"]["summary"] == "x" * 16_000


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
