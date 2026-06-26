"""OpenAI-compatible Chat Completions boundary for Main Agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

from app.core.config import Settings, get_settings


class MainAgentProviderConfigurationError(Exception):
    """Raised when Main Agent provider settings are incomplete."""


class MainAgentProviderError(Exception):
    """Raised when a Main Agent provider request fails."""


class MainAgentChatClient(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str,
        stream: bool = False,
    ) -> Any:
        """Return a Chat Completions response object or compatible mapping."""


@dataclass(frozen=True)
class OpenAICompatibleChatClient:
    """Small sync Chat Completions client used by the Main Agent tool loop."""

    api_key: str | None
    base_url: str | None
    timeout_seconds: int = 120
    max_retries: int = 1
    captured_requests: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenAICompatibleChatClient:
        config = settings or get_settings()
        return cls(
            api_key=config.main_agent_api_key or config.openai_api_key,
            base_url=config.main_agent_base_url,
            timeout_seconds=config.main_agent_timeout_seconds,
        )

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str,
        stream: bool = False,
    ) -> Any:
        if not self.api_key:
            raise MainAgentProviderConfigurationError(
                "MAIN_AGENT_API_KEY or OPENAI_API_KEY is required for Main Agent execution"
            )

        request = {
            "model": model,
            "messages": messages,
            "stream": bool(stream),
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        if stream:
            request["stream_options"] = {"include_usage": True}
        self.captured_requests.append(request)

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=float(self.timeout_seconds),
                max_retries=self.max_retries,
            )
            return client.chat.completions.create(**request)
        except Exception as exc:
            raise MainAgentProviderError(
                "Main Agent provider request failed"
            ) from exc
