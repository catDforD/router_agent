from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_base_health_endpoints_return_same_liveness_payload() -> None:
    settings = Settings(app_name="router-backend", app_env="local")

    with TestClient(create_app(settings)) as client:
        root_response = client.get("/health")
        api_response = client.get("/api/health")

    expected = {
        "status": "ok",
        "app": "router-backend",
        "env": "local",
    }
    assert root_response.status_code == 200
    assert api_response.status_code == 200
    assert root_response.json() == expected
    assert api_response.json() == expected


def test_base_health_ignores_unavailable_external_dependencies() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+psycopg://missing:missing@127.0.0.1:1/missing",
        artifact_root=Path("/path/that/does/not/exist"),
        openai_api_key=None,
        mcp_mode="real",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "router-backend",
        "env": "test",
    }
