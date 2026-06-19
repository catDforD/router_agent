from typing import Any

import pytest

from app.agents.chat_completions import (
    MainAgentProviderConfigurationError,
    OpenAICompatibleChatClient,
)
from app.core.config import Settings


def test_openai_compatible_client_constructs_tool_request_without_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_requests: list[dict[str, Any]] = []

    class FakeCompletions:
        def create(self, **request: Any) -> dict[str, Any]:
            created_requests.append(request)
            return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.chat = FakeChat()

    monkeypatch.setattr("app.agents.chat_completions.OpenAI", FakeOpenAI)
    client = OpenAICompatibleChatClient(
        api_key="main-agent-key",
        base_url="https://provider.example/v1",
        timeout_seconds=30,
    )

    response = client.complete(
        model="provider-model",
        messages=[{"role": "user", "content": "state"}],
        tools=[{"type": "function", "function": {"name": "update_plan"}}],
        stream=False,
    )

    assert response["choices"][0]["message"]["content"] == "ok"
    assert client.captured_requests == created_requests
    assert created_requests[0]["model"] == "provider-model"
    assert created_requests[0]["messages"] == [{"role": "user", "content": "state"}]
    assert created_requests[0]["tools"][0]["function"]["name"] == "update_plan"
    assert created_requests[0]["tool_choice"] == "auto"
    assert created_requests[0]["stream"] is False
    assert "response_format" not in created_requests[0]


def test_openai_compatible_client_requires_main_agent_or_openai_key() -> None:
    client = OpenAICompatibleChatClient(api_key=None, base_url=None)

    with pytest.raises(MainAgentProviderConfigurationError):
        client.complete(model="model", messages=[], tools=[])


def test_openai_compatible_client_loads_main_agent_settings_before_fallback() -> None:
    settings = Settings(
        openai_api_key="openai-key",
        main_agent_api_key="main-agent-key",
        main_agent_base_url="https://provider.example/v1",
        main_agent_timeout_seconds=45,
    )

    client = OpenAICompatibleChatClient.from_settings(settings)

    assert client.api_key == "main-agent-key"
    assert client.base_url == "https://provider.example/v1"
    assert client.timeout_seconds == 45
