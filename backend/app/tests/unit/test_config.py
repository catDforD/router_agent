from pathlib import Path

import pytest

from app.core.config import Settings, get_settings


ENV_KEYS = (
    "APP_NAME",
    "APP_ENV",
    "DATABASE_URL",
    "ARTIFACT_ROOT",
    "SESSION_WORKSPACE_ROOT",
    "OPENAI_API_KEY",
    "MAIN_AGENT_PROVIDER",
    "MAIN_AGENT_API_KEY",
    "MAIN_AGENT_BASE_URL",
    "MAIN_AGENT_MODEL",
    "MAIN_AGENT_MAX_TURNS",
    "MAIN_AGENT_TIMEOUT_SECONDS",
    "MAIN_AGENT_HTTP_BACKEND",
    "MAIN_AGENT_STREAM",
    "MAIN_AGENT_CAPTURE_PROVIDER_TRANSCRIPT",
    "AGENT_EXECUTION_MODE",
    "AGENT_WORKSPACE_ROOT",
    "AGENT_COMMAND_TIMEOUT_SECONDS",
    "AGENT_TOOL_OUTPUT_MAX_CHARS",
    "MCP_MODE",
    "PLC_WORKER_MCP_URL",
    "PLC_WORKER_TIMEOUT_SECONDS",
    "PLC_WORKER_ARTIFACT_MAX_CHARS",
    "SUBAGENT_API_BASE_URL",
    "SUBAGENT_API_TOKEN",
    "SUBAGENT_TIMEOUT_SECONDS",
    "SUBAGENT_MAX_RETRIES",
    "SUBAGENT_RETRY_BACKOFF_SECONDS",
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
    assert settings.session_workspace_root == Path("data/workspaces")
    assert settings.openai_api_key is None
    assert settings.main_agent_provider == "openai_compatible"
    assert settings.main_agent_api_key is None
    assert settings.main_agent_base_url is None
    assert settings.main_agent_model is None
    assert settings.main_agent_max_turns == 20
    assert settings.main_agent_timeout_seconds == 120
    assert settings.main_agent_http_backend == "openai_sdk"
    assert settings.main_agent_stream is True
    assert settings.main_agent_capture_provider_transcript is False
    assert settings.agent_execution_mode == "disabled"
    assert settings.agent_workspace_root == Path(".")
    assert settings.agent_command_timeout_seconds == 120
    assert settings.agent_tool_output_max_chars == 12_000
    assert settings.mcp_mode == "mock"
    assert settings.plc_worker_mcp_url == "http://localhost:9000/mcp"
    assert settings.plc_worker_timeout_seconds == 300
    assert settings.plc_worker_artifact_max_chars == 12_000
    assert settings.subagent_api_base_url == "http://60.188.37.6:28080"
    assert settings.subagent_api_token is None
    assert settings.subagent_timeout_seconds == 300
    assert settings.subagent_max_retries == 2
    assert settings.subagent_retry_backoff_seconds == 1.0
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
    monkeypatch.setenv("SESSION_WORKSPACE_ROOT", "/tmp/router-workspaces")
    monkeypatch.setenv("MAIN_AGENT_PROVIDER", "OPENAI_COMPATIBLE")
    monkeypatch.setenv("MAIN_AGENT_API_KEY", "main-agent-secret")
    monkeypatch.setenv("MAIN_AGENT_BASE_URL", "https://user:pass@main-agent.example/v1")
    monkeypatch.setenv("MAIN_AGENT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("MAIN_AGENT_MAX_TURNS", "12")
    monkeypatch.setenv("MAIN_AGENT_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("MAIN_AGENT_HTTP_BACKEND", "CURL")
    monkeypatch.setenv("MAIN_AGENT_STREAM", "false")
    monkeypatch.setenv("MAIN_AGENT_CAPTURE_PROVIDER_TRANSCRIPT", "true")
    monkeypatch.setenv("AGENT_EXECUTION_MODE", "LOCAL_FULL_ACCESS")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", "/tmp/router-agent-workspace")
    monkeypatch.setenv("AGENT_COMMAND_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AGENT_TOOL_OUTPUT_MAX_CHARS", "2048")
    monkeypatch.setenv("MCP_MODE", "HYBRID")
    monkeypatch.setenv("PLC_WORKER_MCP_URL", "http://worker.example/mcp")
    monkeypatch.setenv("PLC_WORKER_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("PLC_WORKER_ARTIFACT_MAX_CHARS", "4096")
    monkeypatch.setenv("SUBAGENT_API_BASE_URL", "http://subagent.example")
    monkeypatch.setenv("SUBAGENT_API_TOKEN", "subagent-secret-value")
    monkeypatch.setenv("SUBAGENT_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("SUBAGENT_MAX_RETRIES", "4")
    monkeypatch.setenv("SUBAGENT_RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("PLC_DEV_MODE", "SUBAGENT")
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
    assert settings.session_workspace_root == Path("/tmp/router-workspaces")
    assert settings.main_agent_provider == "openai_compatible"
    assert settings.main_agent_api_key == "main-agent-secret"
    assert settings.main_agent_base_url == "https://user:pass@main-agent.example/v1"
    assert settings.main_agent_model == "gpt-4.1-mini"
    assert settings.main_agent_max_turns == 12
    assert settings.main_agent_timeout_seconds == 90
    assert settings.main_agent_http_backend == "curl"
    assert settings.main_agent_stream is False
    assert settings.main_agent_capture_provider_transcript is True
    assert settings.agent_execution_mode == "local_full_access"
    assert settings.agent_workspace_root == Path("/tmp/router-agent-workspace")
    assert settings.agent_command_timeout_seconds == 30
    assert settings.agent_tool_output_max_chars == 2048
    assert settings.mcp_mode == "hybrid"
    assert settings.plc_worker_mcp_url == "http://worker.example/mcp"
    assert settings.plc_worker_timeout_seconds == 45
    assert settings.plc_worker_artifact_max_chars == 4096
    assert settings.subagent_api_base_url == "http://subagent.example"
    assert settings.subagent_api_token == "subagent-secret-value"
    assert settings.subagent_timeout_seconds == 120
    assert settings.subagent_max_retries == 4
    assert settings.subagent_retry_backoff_seconds == 0.5
    assert settings.plc_dev_mode == "subagent"
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

    monkeypatch.setenv("PLC_DEV_MODE", "mock")
    monkeypatch.setenv("MAIN_AGENT_PROVIDER", "unsupported")
    with pytest.raises(ValueError, match="main_agent_provider"):
        Settings()

    monkeypatch.setenv("MAIN_AGENT_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MAIN_AGENT_HTTP_BACKEND", "unsupported")
    with pytest.raises(ValueError, match="main_agent_http_backend"):
        Settings()


def test_subagent_modes_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("MCP_MODE", "SUBAGENT")
    monkeypatch.setenv("PLC_TEST_MODE", "SUBAGENT")

    settings = Settings()

    assert settings.mcp_mode == "subagent"
    assert settings.plc_test_mode == "subagent"


def test_redacted_diagnostics_do_not_expose_secrets() -> None:
    settings = Settings(
        openai_api_key="openai-secret-value",
        main_agent_api_key="main-agent-secret-value",
        main_agent_base_url="https://user:password@main-agent.example/v1",
        deepseek_api_key="deepseek-secret-value",
        subagent_api_token="subagent-secret-value",
    )

    diagnostics = settings.redacted_diagnostics()

    assert diagnostics["openai_api_key"] == "open...alue"
    assert diagnostics["main_agent_api_key"] == "main...alue"
    assert diagnostics["main_agent_base_url"] == "https://[redacted]@main-agent.example/v1"
    assert diagnostics["deepseek_api_key"] == "deep...alue"
    assert diagnostics["subagent_api_token"] == "suba...alue"
    assert diagnostics["session_workspace_root"] == "data/workspaces"
    assert diagnostics["main_agent_capture_provider_transcript"] is False
    assert diagnostics["agent_execution_mode"] == "disabled"
    assert diagnostics["agent_workspace_root"] == "."
    assert diagnostics["agent_tool_output_max_chars"] == 12_000
    assert "openai-secret-value" not in str(diagnostics)
    assert "main-agent-secret-value" not in str(diagnostics)
    assert "user:password" not in str(diagnostics)
    assert "deepseek-secret-value" not in str(diagnostics)
    assert "subagent-secret-value" not in str(diagnostics)
    assert diagnostics["deepseek_model"] == "deepseek-chat"
