"""MCP client boundary for real PLC worker dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp.draft import (
    LlmWorkerDraftOutput,
    McpDraftValidationError,
    McpWorkerRequest,
    parse_worker_draft_output,
)


class PlcMcpClientError(Exception):
    """Base class for real MCP client failures."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class PlcMcpConnectionError(PlcMcpClientError):
    """Raised when the MCP server cannot be reached."""


class PlcMcpTimeoutError(PlcMcpClientError):
    """Raised when MCP initialization or tool execution times out."""


class PlcMcpToolNotFoundError(PlcMcpClientError):
    """Raised when the configured MCP server does not expose a required tool."""


class PlcMcpInvalidResponseError(PlcMcpClientError):
    """Raised when an MCP tool response cannot be parsed as a worker draft."""


class PlcMcpToolError(PlcMcpClientError):
    """Raised when an MCP tool explicitly returns an error result."""


class PlcMcpSession(Protocol):
    async def list_tool_names(self) -> list[str]:
        """Return available MCP tool names."""

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call one MCP tool and return its raw result."""


SessionFactory = Callable[[], AsyncIterator[PlcMcpSession]]


@dataclass(frozen=True)
class PlcMcpClient:
    """Synchronous Router-facing wrapper over the async MCP SDK client."""

    url: str
    timeout_seconds: int = 300
    session_factory: SessionFactory | None = None

    def list_tools(self) -> list[str]:
        """List available tools on the configured MCP server."""

        return _run_sync(self._list_tools_async())

    def call_worker_tool(
        self,
        tool_name: str,
        request: McpWorkerRequest,
    ) -> LlmWorkerDraftOutput:
        """Call one PLC MCP worker tool and parse the returned draft output."""

        return _run_sync(self._call_worker_tool_async(tool_name, request))

    async def _list_tools_async(self) -> list[str]:
        try:
            async with self._session() as session:
                return await session.list_tool_names()
        except TimeoutError as exc:
            raise PlcMcpTimeoutError(
                "MCP tool discovery timed out",
                details={"url": self.url},
            ) from exc
        except httpx.TimeoutException as exc:
            raise PlcMcpTimeoutError(
                "MCP tool discovery timed out",
                details={"url": self.url},
            ) from exc
        except httpx.HTTPError as exc:
            raise PlcMcpConnectionError(
                "MCP server cannot be reached",
                details={"url": self.url, "exception_type": type(exc).__name__},
            ) from exc

    async def _call_worker_tool_async(
        self,
        tool_name: str,
        request: McpWorkerRequest,
    ) -> LlmWorkerDraftOutput:
        try:
            async with self._session() as session:
                available = await session.list_tool_names()
                if tool_name not in available:
                    raise PlcMcpToolNotFoundError(
                        f"MCP tool is not available: {tool_name}",
                        details={"tool_name": tool_name, "available_tools": available},
                    )
                raw_result = await session.call_tool(
                    tool_name,
                    request.model_dump(mode="json"),
                )
                return parse_mcp_tool_result(raw_result)
        except PlcMcpClientError:
            raise
        except TimeoutError as exc:
            raise PlcMcpTimeoutError(
                "MCP tool call timed out",
                details={"url": self.url, "tool_name": tool_name},
            ) from exc
        except httpx.TimeoutException as exc:
            raise PlcMcpTimeoutError(
                "MCP tool call timed out",
                details={"url": self.url, "tool_name": tool_name},
            ) from exc
        except httpx.HTTPError as exc:
            raise PlcMcpConnectionError(
                "MCP server cannot be reached",
                details={
                    "url": self.url,
                    "tool_name": tool_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[PlcMcpSession]:
        if self.session_factory is not None:
            async with self.session_factory() as session:
                yield session
            return

        timeout = httpx.Timeout(float(self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                ) as session:
                    await session.initialize()
                    yield _SdkPlcMcpSession(session)


class _SdkPlcMcpSession:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def list_tool_names(self) -> list[str]:
        result = await self.session.list_tools()
        return [tool.name for tool in result.tools]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        return await self.session.call_tool(tool_name, arguments=arguments)


def parse_mcp_tool_result(raw_result: Any) -> LlmWorkerDraftOutput:
    """Parse a Python MCP SDK CallToolResult into a worker draft output."""

    if getattr(raw_result, "isError", False):
        raise PlcMcpToolError("MCP tool returned an error result")

    structured = getattr(raw_result, "structuredContent", None)
    if structured is not None:
        try:
            return parse_worker_draft_output(structured)
        except McpDraftValidationError as exc:
            raise PlcMcpInvalidResponseError(
                "MCP structured response is not a valid worker draft",
                details=exc.details,
            ) from exc

    content = getattr(raw_result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text is None:
            continue
        try:
            return parse_worker_draft_output(text)
        except McpDraftValidationError as exc:
            raise PlcMcpInvalidResponseError(
                "MCP text response is not a valid worker draft",
                details=exc.details,
            ) from exc

    raise PlcMcpInvalidResponseError("MCP tool response does not contain worker draft content")


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise PlcMcpClientError(
        "PlcMcpClient synchronous API cannot run inside an active event loop"
    )
