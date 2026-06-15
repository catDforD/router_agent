"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    mcp_mode: str = Field(default="mock", validation_alias="MCP_MODE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    """Return cached settings without opening external connections."""

    return Settings()
