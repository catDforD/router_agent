"""List tools exposed by the configured PLC worker MCP server."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.mcp.client import PlcMcpClient  # noqa: E402


def main() -> None:
    settings = get_settings()
    diagnostics = settings.redacted_diagnostics()
    print("PLC worker MCP diagnostics:")
    for key in (
        "mcp_mode",
        "plc_worker_mcp_url",
        "plc_worker_timeout_seconds",
        "deepseek_base_url",
        "deepseek_model",
        "deepseek_api_key",
    ):
        print(f"  {key}: {diagnostics[key]}")

    tools = PlcMcpClient(
        url=settings.plc_worker_mcp_url,
        timeout_seconds=settings.plc_worker_timeout_seconds,
    ).list_tools()
    print("Available tools:")
    for tool in tools:
        print(f"  - {tool}")


if __name__ == "__main__":
    main()
