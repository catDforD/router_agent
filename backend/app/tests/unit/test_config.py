from pathlib import Path

import pytest

from app.core.config import Settings, get_settings


ENV_KEYS = (
    "APP_NAME",
    "APP_ENV",
    "DATABASE_URL",
    "ARTIFACT_ROOT",
    "OPENAI_API_KEY",
    "MAIN_AGENT_MODEL",
    "MAIN_AGENT_MAX_TURNS",
    "MCP_MODE",
    "PLC_WORKER_MCP_URL",
    "PLC_WORKER_TIMEOUT_SECONDS",
    "PLC_WORKER_ARTIFACT_MAX_CHARS",
    "PLC_DEV_MODE",
    "PLC_TEST_MODE",
    "PLC_FORMAL_MODE",
    "PLC_REPAIR_MODE",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_TIMEOUT_SECONDS",
    "DEEPSEEK_MAX_RETRIES",
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
    assert settings.main_agent_model is None
    assert settings.main_agent_max_turns == 20
    assert settings.mcp_mode == "mock"
    assert settings.plc_worker_mcp_url == "http://localhost:9000/mcp"
    assert settings.plc_worker_timeout_seconds == 300
    assert settings.plc_worker_artifact_max_chars == 12_000
    assert settings.plc_dev_mode is None
    assert settings.plc_test_mode is None
    assert settings.plc_formal_mode is None
    assert settings.plc_repair_mode is None
    assert settings.deepseek_api_key is None
    assert settings.deepseek_base_url == "https://api.deepseek.com/v1"
    assert settings.deepseek_model == "deepseek-chat"
    assert settings.deepseek_timeout_seconds == 60
    assert settings.deepseek_max_retries == 1
    assert settings.mock_scenario == "dev_test_pass"
    assert settings.log_level == "INFO"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@db:5432/router_test")
    monkeypatch.setenv("ARTIFACT_ROOT", "/tmp/router-artifacts")
    monkeypatch.setenv("MAIN_AGENT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("MAIN_AGENT_MAX_TURNS", "12")
    monkeypatch.setenv("MCP_MODE", "HYBRID")
    monkeypatch.setenv("PLC_WORKER_MCP_URL", "http://worker.example/mcp")
    monkeypatch.setenv("PLC_WORKER_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("PLC_WORKER_ARTIFACT_MAX_CHARS", "4096")
    monkeypatch.setenv("PLC_DEV_MODE", "REAL")
    monkeypatch.setenv("PLC_TEST_MODE", "mock")
    monkeypatch.setenv("PLC_FORMAL_MODE", "real")
    monkeypatch.setenv("PLC_REPAIR_MODE", "MOCK")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-worker")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("DEEPSEEK_MAX_RETRIES", "2")
    monkeypatch.setenv("MOCK_SCENARIO", "worker_timeout")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql+psycopg://test:test@db:5432/router_test"
    assert settings.artifact_root == Path("/tmp/router-artifacts")
    assert settings.main_agent_model == "gpt-4.1-mini"
    assert settings.main_agent_max_turns == 12
    assert settings.mcp_mode == "hybrid"
    assert settings.plc_worker_mcp_url == "http://worker.example/mcp"
    assert settings.plc_worker_timeout_seconds == 45
    assert settings.plc_worker_artifact_max_chars == 4096
    assert settings.plc_dev_mode == "real"
    assert settings.plc_test_mode == "mock"
    assert settings.plc_formal_mode == "real"
    assert settings.plc_repair_mode == "mock"
    assert settings.deepseek_api_key == "deepseek-secret-value"
    assert settings.deepseek_base_url == "https://deepseek.example/v1"
    assert settings.deepseek_model == "deepseek-worker"
    assert settings.deepseek_timeout_seconds == 20
    assert settings.deepseek_max_retries == 2
    assert settings.mock_scenario == "worker_timeout"
    assert settings.log_level == "DEBUG"


def test_invalid_modes_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("MCP_MODE", "unsupported")
    with pytest.raises(ValueError, match="mcp_mode"):
        Settings()

    monkeypatch.setenv("MCP_MODE", "mock")
    monkeypatch.setenv("PLC_DEV_MODE", "hybrid")
    with pytest.raises(ValueError, match="PLC worker mode"):
        Settings()


def test_redacted_diagnostics_do_not_expose_secrets() -> None:
    settings = Settings(
        openai_api_key="openai-secret-value",
        deepseek_api_key="deepseek-secret-value",
    )

    diagnostics = settings.redacted_diagnostics()

    assert diagnostics["openai_api_key"] == "open...alue"
    assert diagnostics["deepseek_api_key"] == "deep...alue"
    assert "openai-secret-value" not in str(diagnostics)
    assert "deepseek-secret-value" not in str(diagnostics)
    assert diagnostics["deepseek_model"] == "deepseek-chat"
