## Why

Router can complete deterministic mock worker flows, but `MCP_MODE=real` is not implemented and the backend cannot yet exercise the real MCP transport boundary for `plc-dev`, `plc-test`, `plc-formal`, or `plc-repair`. We need a local MCP server that preserves the Router worker contract while simulating the future subagents through DeepSeek OpenAI-compatible chat completions until the real subagent interfaces are available.

## What Changes

- Add a local PLC worker MCP server exposing `plc_dev.run`, `plc_test.run`, `plc_formal.run`, and `plc_repair.run`.
- Add a Router MCP client path for streamable HTTP MCP calls when `MCP_MODE=real`.
- Add hybrid worker mode configuration so each PLC worker can independently use the MCP server or the existing mock worker.
- Add a DeepSeek-backed worker simulator used only inside the MCP server, configured from `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`.
- Keep Main Agent model execution separate from worker simulation; Main Agent continues to use the existing `OPENAI_API_KEY`/Main Agent configuration.
- Persist LLM-produced worker artifacts through Router's existing Artifact Store before returning standard Router v1 `WorkerResult` values.
- Add contract and integration coverage for the MCP transport, worker result normalization, and the four PLC worker flows.

## Capabilities

### New Capabilities

- `llm-backed-plc-mcp-server`: Provides a local MCP server and Router MCP client path for LLM-backed PLC worker simulation across development, testing, formal verification, and repair.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `backend/app/core/config.py`
  - `backend/app/mcp/client.py`
  - `backend/app/mcp/adapter.py`
  - new MCP server and worker-simulation modules under `backend/app/mcp/` or a script/module entrypoint
  - tests under `backend/app/tests/unit/` and `backend/app/tests/integration/`
  - local smoke scripts under `scripts/`
  - `.env.example` and local development documentation
- Runtime dependencies may need to include MCP/OpenAI/httpx client packages used outside test-only code.
- Existing public HTTP APIs remain unchanged.
- Router v1 Pydantic models, exported JSON Schema files, and TypeScript declarations remain unchanged.
- Existing mock worker behavior remains available and remains the default local mode.
