"""OpenAI-compatible Chat Completions boundary for Main Agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Protocol

from openai import OpenAI
from openai import APIConnectionError

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
    http_backend: str = "openai_sdk"
    captured_requests: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenAICompatibleChatClient:
        config = settings or get_settings()
        return cls(
            api_key=config.main_agent_api_key or config.openai_api_key,
            base_url=config.main_agent_base_url,
            timeout_seconds=config.main_agent_timeout_seconds,
            http_backend=config.main_agent_http_backend,
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

        if self.http_backend == "curl":
            if stream:
                raise MainAgentProviderConfigurationError(
                    "MAIN_AGENT_HTTP_BACKEND=curl does not support streaming"
                )
            return self._complete_with_curl(request)

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=float(self.timeout_seconds),
                max_retries=self.max_retries,
            )
            return client.chat.completions.create(**request)
        except APIConnectionError as exc:
            if self.http_backend == "auto" and not stream:
                try:
                    return self._complete_with_curl(request)
                except MainAgentProviderError:
                    pass
            raise MainAgentProviderError(
                "Main Agent provider request failed"
            ) from exc
        except Exception as exc:
            raise MainAgentProviderError(
                "Main Agent provider request failed"
            ) from exc

    def _complete_with_curl(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise MainAgentProviderConfigurationError(
                "MAIN_AGENT_BASE_URL is required when MAIN_AGENT_HTTP_BACKEND=curl"
            )
        url = _join_url(self.base_url, "chat/completions")
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            response_path = Path(temp_dir) / "response.json"
            config_path = Path(temp_dir) / "curl.conf"
            request_path.write_text(
                json.dumps(request, ensure_ascii=False),
                encoding="utf-8",
            )
            config_path.write_text(
                "\n".join(
                    [
                        "silent",
                        "show-error",
                        "fail-with-body",
                        f"max-time = {max(int(self.timeout_seconds), 1)}",
                        f"url = {json.dumps(url)}",
                        'header = "Content-Type: application/json"',
                        f"header = {json.dumps(f'Authorization: Bearer {self.api_key}')}",
                        f"data-binary = {json.dumps(f'@{request_path}')}",
                        f"output = {json.dumps(str(response_path))}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            command = ["curl", "--config", str(config_path)]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds + 5,
                )
            except Exception as exc:
                raise MainAgentProviderError(
                    "Main Agent provider curl request failed"
                ) from exc

            response_text = (
                response_path.read_text(encoding="utf-8")
                if response_path.is_file()
                else ""
            )
            if completed.returncode != 0:
                raise MainAgentProviderError(
                    "Main Agent provider curl request failed: "
                    f"{_bounded_error(completed.stderr or response_text)}"
                )
            try:
                response = json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise MainAgentProviderError(
                    "Main Agent provider returned invalid JSON"
                ) from exc
            if not isinstance(response, dict):
                raise MainAgentProviderError(
                    "Main Agent provider response must be a JSON object"
                )
            if "error" in response:
                raise MainAgentProviderError(
                    "Main Agent provider returned error: "
                    f"{_bounded_error(json.dumps(response['error'], ensure_ascii=False))}"
                )
            return response


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _bounded_error(value: str, limit: int = 500) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 3, 0)]}..."
