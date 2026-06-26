"""Local LLM-backed PLC worker MCP server."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from app.core.config import Settings, get_settings
from app.mcp.llm_worker import LlmPlcWorkerService


def create_plc_worker_mcp_server(
    *,
    settings: Settings | None = None,
    service: LlmPlcWorkerService | None = None,
) -> FastMCP:
    """Create the local PLC worker MCP server."""

    config = settings or get_settings()
    worker_service = service or LlmPlcWorkerService.from_settings(config)
    host, port, path = _server_bind(config.plc_worker_mcp_url)

    mcp = FastMCP(
        "Router PLC Worker Simulator",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(name="plc_dev.run", structured_output=True)
    def plc_dev_run(
        worker_input: dict[str, Any],
        input_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Simulate PLC development from a Router WorkerInput."""

        return worker_service.run_tool(
            "plc_dev.run",
            _payload(worker_input, input_files),
        )

    @mcp.tool(name="plc_test.run", structured_output=True)
    def plc_test_run(
        worker_input: dict[str, Any],
        input_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Simulate PLC testing from a Router WorkerInput."""

        return worker_service.run_tool(
            "plc_test.run",
            _payload(worker_input, input_files),
        )

    @mcp.tool(name="plc_formal.run", structured_output=True)
    def plc_formal_run(
        worker_input: dict[str, Any],
        input_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Simulate PLC formal verification from a Router WorkerInput."""

        return worker_service.run_tool(
            "plc_formal.run",
            _payload(worker_input, input_files),
        )

    @mcp.tool(name="plc_repair.run", structured_output=True)
    def plc_repair_run(
        worker_input: dict[str, Any],
        input_files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Simulate PLC repair from a Router WorkerInput."""

        return worker_service.run_tool(
            "plc_repair.run",
            _payload(worker_input, input_files),
        )

    return mcp


def main() -> None:
    """Run the local PLC worker MCP server using environment settings."""

    create_plc_worker_mcp_server().run(transport="streamable-http")


def _payload(
    worker_input: dict[str, Any],
    input_files: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "worker_input": worker_input,
        "input_files": input_files or [],
    }


def _server_bind(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/mcp"
    return host, port, path


if __name__ == "__main__":
    main()
