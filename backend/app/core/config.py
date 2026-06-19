"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime settings for the Router backend process."""

    app_name: str = Field(default="router-backend", validation_alias="APP_NAME")
    app_env: str = Field(default="local", validation_alias="APP_ENV")
    database_url: str = Field(
        default="postgresql+psycopg://router:router@localhost:5432/router",
        validation_alias="DATABASE_URL",
    )
    artifact_root: Path = Field(
        default=Path("./data/artifacts"),
        validation_alias="ARTIFACT_ROOT",
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    main_agent_provider: str = Field(
        default="openai_compatible",
        validation_alias="MAIN_AGENT_PROVIDER",
    )
    main_agent_api_key: str | None = Field(
        default=None,
        validation_alias="MAIN_AGENT_API_KEY",
    )
    main_agent_base_url: str | None = Field(
        default=None,
        validation_alias="MAIN_AGENT_BASE_URL",
    )
    main_agent_model: str | None = Field(
        default=None,
        validation_alias="MAIN_AGENT_MODEL",
    )
    main_agent_max_turns: int = Field(
        default=20,
        ge=1,
        validation_alias="MAIN_AGENT_MAX_TURNS",
    )
    main_agent_timeout_seconds: int = Field(
        default=120,
        ge=1,
        validation_alias="MAIN_AGENT_TIMEOUT_SECONDS",
    )
    main_agent_stream: bool = Field(
        default=True,
        validation_alias="MAIN_AGENT_STREAM",
    )
    mcp_mode: str = Field(default="mock", validation_alias="MCP_MODE")
    plc_worker_mcp_url: str = Field(
        default="http://localhost:9000/mcp",
        validation_alias="PLC_WORKER_MCP_URL",
    )
    plc_worker_timeout_seconds: int = Field(
        default=300,
        ge=1,
        validation_alias="PLC_WORKER_TIMEOUT_SECONDS",
    )
    plc_worker_artifact_max_chars: int = Field(
        default=12_000,
        ge=1,
        validation_alias="PLC_WORKER_ARTIFACT_MAX_CHARS",
    )
    plc_dev_mode: str | None = Field(default=None, validation_alias="PLC_DEV_MODE")
    plc_test_mode: str | None = Field(default=None, validation_alias="PLC_TEST_MODE")
    plc_formal_mode: str | None = Field(default=None, validation_alias="PLC_FORMAL_MODE")
    plc_repair_mode: str | None = Field(default=None, validation_alias="PLC_REPAIR_MODE")
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        validation_alias="DEEPSEEK_MODEL",
    )
    deepseek_timeout_seconds: int = Field(
        default=60,
        ge=1,
        validation_alias="DEEPSEEK_TIMEOUT_SECONDS",
    )
    deepseek_max_retries: int = Field(
        default=1,
        ge=0,
        validation_alias="DEEPSEEK_MAX_RETRIES",
    )
    mock_scenario: str = Field(
        default="dev_test_pass",
        validation_alias="MOCK_SCENARIO",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("mcp_mode")
    @classmethod
    def normalize_mcp_mode(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"mock", "real", "hybrid"}:
            raise ValueError("mcp_mode must be 'mock', 'real', or 'hybrid'")
        return normalized

    @field_validator("main_agent_provider")
    @classmethod
    def normalize_main_agent_provider(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"openai_compatible", "legacy_openai_agents"}:
            raise ValueError(
                "main_agent_provider must be 'openai_compatible' or "
                "'legacy_openai_agents'"
            )
        return normalized

    @field_validator(
        "plc_dev_mode",
        "plc_test_mode",
        "plc_formal_mode",
        "plc_repair_mode",
    )
    @classmethod
    def normalize_worker_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if normalized not in {"mock", "real"}:
            raise ValueError("PLC worker mode must be 'mock' or 'real'")
        return normalized

    @model_validator(mode="after")
    def normalize_hybrid_defaults(self) -> Settings:
        if self.mcp_mode == "hybrid":
            return self
        return self

    def redacted_diagnostics(self) -> dict[str, str | int | bool | None]:
        """Return non-secret settings useful for local diagnostics."""

        return {
            "app_env": self.app_env,
            "mcp_mode": self.mcp_mode,
            "plc_worker_mcp_url": self.plc_worker_mcp_url,
            "plc_worker_timeout_seconds": self.plc_worker_timeout_seconds,
            "plc_worker_artifact_max_chars": self.plc_worker_artifact_max_chars,
            "plc_dev_mode": self.plc_dev_mode,
            "plc_test_mode": self.plc_test_mode,
            "plc_formal_mode": self.plc_formal_mode,
            "plc_repair_mode": self.plc_repair_mode,
            "main_agent_provider": self.main_agent_provider,
            "main_agent_base_url": _redacted_url(self.main_agent_base_url),
            "main_agent_model": self.main_agent_model,
            "main_agent_timeout_seconds": self.main_agent_timeout_seconds,
            "main_agent_max_turns": self.main_agent_max_turns,
            "main_agent_stream": self.main_agent_stream,
            "main_agent_api_key": _redacted(self.main_agent_api_key),
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "deepseek_timeout_seconds": self.deepseek_timeout_seconds,
            "deepseek_max_retries": self.deepseek_max_retries,
            "deepseek_api_key": _redacted(self.deepseek_api_key),
            "openai_api_key": _redacted(self.openai_api_key),
        }


@lru_cache
def get_settings() -> Settings:
    """Return cached settings without opening external connections."""

    return Settings()


def _redacted(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _redacted_url(value: str | None) -> str | None:
    if value is None:
        return None
    if "@" not in value:
        return value
    scheme, _, rest = value.partition("://")
    if not rest:
        return value
    _, _, host_path = rest.rpartition("@")
    return f"{scheme}://[redacted]@{host_path}" if scheme else f"[redacted]@{host_path}"
