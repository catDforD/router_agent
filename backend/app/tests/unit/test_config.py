from pathlib import Path

import pytest

from app.core.config import Settings, get_settings


ENV_KEYS = (
    "APP_NAME",
    "APP_ENV",
    "DATABASE_URL",
    "ARTIFACT_ROOT",
    "OPENAI_API_KEY",
    "MCP_MODE",
    "MOCK_SCENARIO",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clear_cached_settings() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_defaults_support_local_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.app_name == "router-backend"
    assert settings.app_env == "local"
    assert settings.database_url == "postgresql+psycopg://router:router@localhost:5432/router"
    assert settings.artifact_root == Path("data/artifacts")
    assert settings.openai_api_key is None
    assert settings.mcp_mode == "mock"
    assert settings.mock_scenario == "dev_test_pass"
    assert settings.log_level == "INFO"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@db:5432/router_test")
    monkeypatch.setenv("ARTIFACT_ROOT", "/tmp/router-artifacts")
    monkeypatch.setenv("MCP_MODE", "real")
    monkeypatch.setenv("MOCK_SCENARIO", "worker_timeout")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql+psycopg://test:test@db:5432/router_test"
    assert settings.artifact_root == Path("/tmp/router-artifacts")
    assert settings.mcp_mode == "real"
    assert settings.mock_scenario == "worker_timeout"
    assert settings.log_level == "DEBUG"
