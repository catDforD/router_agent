## 1. Configuration and Dependencies

- [x] 1.1 Add runtime settings for `PLC_WORKER_MCP_URL`, `PLC_WORKER_TIMEOUT_SECONDS`, worker artifact content limits, and `PLC_DEV_MODE` / `PLC_TEST_MODE` / `PLC_FORMAL_MODE` / `PLC_REPAIR_MODE`.
- [x] 1.2 Add worker-server DeepSeek settings for `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, and provider timeout/retry defaults without reusing Main Agent OpenAI settings.
- [x] 1.3 Validate MCP and per-worker mode values so only supported `mock` and `real` worker routes are accepted.
- [x] 1.4 Make direct runtime dependencies explicit for MCP/OpenAI/httpx client usage if current transitive dependencies are insufficient.
- [x] 1.5 Update `.env.example` with safe placeholders for MCP URL, worker modes, and DeepSeek settings.
- [x] 1.6 Add config unit tests for defaults, per-worker overrides, DeepSeek settings, invalid modes, and secret redaction behavior.

## 2. Worker Draft Contract

- [x] 2.1 Add internal Pydantic models for MCP worker request envelopes, bounded input artifact snapshots, artifact write drafts, and LLM worker draft output.
- [x] 2.2 Add worker-specific validation that passed `plc-dev` drafts include `plc_code` and `io_contract`.
- [x] 2.3 Add worker-specific validation that passed `plc-test` drafts include `test_report`.
- [x] 2.4 Add worker-specific validation that passed `plc-formal` drafts include `formal_report`.
- [x] 2.5 Add worker-specific validation that passed `plc-repair` drafts include `patch`, patched `plc_code`, and `repair_summary`.
- [x] 2.6 Add unit tests for valid drafts, missing required artifacts, invalid worker/tool pairing, malformed model JSON, and failed/clarification outcomes.

## 3. MCP Client

- [x] 3.1 Implement `backend/app/mcp/client.py` with streamable HTTP MCP session initialization, `list_tools`, and `call_tool`.
- [x] 3.2 Parse MCP tool responses from structured content or JSON text into the internal draft output model.
- [x] 3.3 Add timeout, connection failure, and invalid response exceptions that do not expose request headers or API keys.
- [x] 3.4 Add an injectable fake MCP session/client boundary for deterministic tests.
- [x] 3.5 Add unit tests for tool discovery, successful tool call parsing, timeout, connection failure, missing tool, and invalid response handling.

## 4. LLM-Backed PLC MCP Server

- [x] 4.1 Add a local MCP server module or entrypoint exposing `plc_dev.run`, `plc_test.run`, `plc_formal.run`, and `plc_repair.run`.
- [x] 4.2 Add a DeepSeek OpenAI-compatible chat client wrapper that uses only `DEEPSEEK_*` settings and supports fake client injection.
- [x] 4.3 Add worker prompts and response parsing for `plc-dev` draft requirements/code/IO contract generation.
- [x] 4.4 Add worker prompts and response parsing for `plc-test` pass/fail report, failing trace, failure, and test metrics generation.
- [x] 4.5 Add worker prompts and response parsing for `plc-formal` pass/fail report, counterexample, failure, and formal metrics generation.
- [x] 4.6 Add worker prompts and response parsing for `plc-repair` patch, patched code, repair summary, and repair metrics generation.
- [x] 4.7 Ensure server-side validation rejects WorkerInput payloads whose worker type does not match the called MCP tool.
- [x] 4.8 Add deterministic unit tests for each MCP tool using a fake DeepSeek client.

## 5. Adapter Integration

- [x] 5.1 Extend `McpAdapter` to select mock or real route from global MCP mode and per-worker mode settings while preserving the existing mock default.
- [x] 5.2 Generate and attach `mcp_request_id` before real MCP dispatch and include it in worker input/result trace context and worker event correlations.
- [x] 5.3 Build bounded artifact content snapshots from Router Artifact Store for real MCP requests.
- [x] 5.4 Persist MCP draft artifact writes through `ArtifactStore` and convert them into final `ArtifactRef` entries.
- [x] 5.5 Construct, normalize, and persist canonical Router v1 `WorkerResult` values from MCP draft output.
- [x] 5.6 Normalize MCP timeout, connection failure, invalid response, and draft validation failures into standard error WorkerResults and worker events.
- [x] 5.7 Add adapter unit tests for real dispatch success, hybrid routing, artifact persistence, trace correlation, schema-invalid draft handling, timeout, and connection failure.

## 6. Integration Coverage

- [x] 6.1 Add `test_mcp_real_contract.py` coverage proving tool discovery, valid WorkerInput dispatch, returned draft parsing, and invalid output rejection.
- [x] 6.2 Add `test_real_plc_dev.py` coverage for LLM-backed `plc-dev` producing non-empty `plc_code` and `io_contract` artifacts and updating current code state.
- [x] 6.3 Add `test_real_plc_test.py` coverage for passing and failing test outcomes, including failure evidence and gate updates.
- [x] 6.4 Add `test_real_plc_formal.py` coverage for passing formal outcomes and failed outcomes with counterexample evidence.
- [x] 6.5 Add `test_real_plc_repair.py` coverage for guard rejection without failure, successful repair artifacts, current code update, repair metadata, and regression flags.
- [x] 6.6 Add a hybrid integration test proving one worker can use real MCP while another uses the existing mock path.

## 7. Local Scripts and Documentation

- [x] 7.1 Add a local script or module command to start the LLM-backed PLC MCP server on the configured MCP URL.
- [x] 7.2 Add a local MCP tool discovery smoke script that prints available tools without printing secrets.
- [x] 7.3 Add opt-in live worker smoke scripts for `plc-dev`, `plc-test`, `plc-formal`, and `plc-repair` using DeepSeek credentials only when explicitly requested.
- [x] 7.4 Document local startup order, required environment variables, hybrid mode examples, and expected smoke-test commands.
- [x] 7.5 Document that LLM-backed test/formal outputs are simulation artifacts until replaced by real PLC subagents.

## 8. Verification

- [x] 8.1 Run focused unit tests for config, draft models, MCP client, MCP server tools, and adapter routing.
- [x] 8.2 Run real/hybrid MCP integration tests without live DeepSeek calls by using fake MCP/DeepSeek clients.
- [x] 8.3 Run existing mock MCP, WorkerResult handler, Agent Tool, Runtime, and mock E2E tests to prove the mock path remains intact.
- [x] 8.4 Run opt-in live DeepSeek smoke scripts locally when credentials are available, without adding them to mandatory CI.
- [x] 8.5 Run `uv run python -m compileall backend`.
- [x] 8.6 Run `git diff --check`.
