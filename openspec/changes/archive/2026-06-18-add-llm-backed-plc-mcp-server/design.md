## Context

The backend already has Router v1 schemas, worker input construction, Scheduler Guard checks, mock MCP workers, WorkerResult handling, Quality Gate, Main Agent function tools, Runtime execution, and mock E2E coverage. The current gap is the real MCP boundary: `backend/app/mcp/client.py` is empty and `McpAdapter` rejects non-mock modes.

The requested implementation should not wait for finished PLC subagent interfaces. Instead, Router should call a real local MCP server whose four PLC tools simulate subagent behavior by calling a DeepSeek OpenAI-compatible chat-completions API. Main Agent model execution is separate and continues to use the existing OpenAI/Main Agent configuration.

## Goals / Non-Goals

**Goals:**

- Implement a real streamable HTTP MCP client path for Router worker dispatch.
- Provide a local MCP server exposing `plc_dev.run`, `plc_test.run`, `plc_formal.run`, and `plc_repair.run`.
- Simulate each worker through DeepSeek configuration (`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`) behind the MCP server.
- Preserve existing Main Agent tools and Router v1 public contracts.
- Persist worker-produced content through the existing Artifact Store before constructing `WorkerResult.produced_artifacts`.
- Support hybrid routing so individual workers can use real MCP or existing mock behavior.
- Add automated contract coverage that does not require live DeepSeek credentials, plus opt-in live smoke scripts.

**Non-Goals:**

- Do not implement the final real subagent interfaces.
- Do not prove PLC tests or formal verification with external PLC toolchains.
- Do not change Router v1 schemas, exported JSON Schema files, TypeScript declarations, database schema, or public HTTP APIs.
- Do not change Main Agent provider configuration or depend on the OpenAI-compatible Main Agent runner change.
- Do not send or log API keys, raw secrets, or unbounded artifact contents.

## Decisions

### Use a local FastMCP server as the real worker boundary

Implement a local MCP server with the Python MCP SDK and streamable HTTP transport. The server exposes the same tool names already encoded in Router v1:

```text
plc_dev.run
plc_test.run
plc_formal.run
plc_repair.run
```

The Router MCP client should initialize an MCP session, list tools for diagnostics/contract checks, and call the selected tool with JSON arguments.

Alternative considered: call DeepSeek directly from `McpAdapter`. Rejected because that would not exercise the real MCP boundary required by `docs/backend.md` step 18 and would make the later subagent swap less representative.

### Keep the Main Agent provider separate from worker LLM simulation

Main Agent continues using existing `OPENAI_API_KEY` and Main Agent settings. The MCP server owns DeepSeek settings and uses them only for worker simulation.

```text
Main Agent provider config        Worker simulation config
OPENAI_API_KEY / MAIN_AGENT_*     DEEPSEEK_API_KEY / DEEPSEEK_*
           |                              |
           v                              v
     MainAgentService              PLC MCP Server
```

Alternative considered: reuse OpenAI settings for both Main Agent and MCP workers. Rejected because the user explicitly needs separate DeepSeek-backed worker simulation and because conflating provider settings makes local diagnosis harder.

### Return worker draft output from MCP, not final WorkerResult artifact refs

The MCP server should return a Router-local draft shape, for example:

```text
LlmWorkerDraftOutput
  outcome
  summary
  artifact_writes[]
  diagnostics[]
  assumptions[]
  failures[]
  clarification_request?
  metrics
  next_recommended_action
  metadata
```

`artifact_writes[]` contains content, type, version, summary, metadata, and MIME type. `McpAdapter` persists those writes through `ArtifactStore`, receives real `ArtifactRef` values, then constructs and validates the canonical Router v1 `WorkerResult`.

Alternative considered: ask the MCP server to return a full `WorkerResult`. Rejected because the MCP server cannot know Router-generated `artifact_id`, `uri`, `content_hash`, or storage metadata before persistence.

### Pass bounded artifact content with the WorkerInput envelope

MCP tools should receive a valid `WorkerInput` plus bounded artifact content snapshots prepared by Router:

```text
{
  "worker_input": { ... Router v1 WorkerInput ... },
  "input_artifacts": [
    {
      "artifact_id": "...",
      "type": "plc_code",
      "version": 1,
      "summary": "...",
      "content": "...",
      "content_truncated": false
    }
  ]
}
```

This keeps `WorkerInput` intact while giving the LLM simulator enough context to read requirements, PLC code, reports, failing traces, and counterexamples. Content limits should be configurable and conservative.

Alternative considered: make the MCP server read Router artifact URIs directly. Rejected for the first version because local file paths, DB access, and future remote subagents would couple the worker server to Router storage internals.

### Route mock, real, and hybrid workers through one adapter

`McpAdapter` should keep the existing mock path unchanged. In real or hybrid mode it should route by worker type:

```text
MCP_MODE=mock   -> all workers mock
MCP_MODE=real   -> workers default to MCP server, per-worker overrides allowed
MCP_MODE=hybrid -> workers use PLC_*_MODE defaults/overrides

PLC_DEV_MODE=real|mock
PLC_TEST_MODE=real|mock
PLC_FORMAL_MODE=real|mock
PLC_REPAIR_MODE=real|mock
```

Alternative considered: one global `MCP_MODE=real` switch only. Rejected because `docs/backend.md` explicitly calls for hybrid rollout while some real workers may be unavailable or unstable.

### Normalize MCP and LLM failures into WorkerResult errors

Connection failures, timeouts, invalid MCP responses, invalid draft JSON, and draft validation failures should become standard error `WorkerResult` values when a worker job has started. Timeouts should use `execution_status=timeout`; schema/model problems should use `execution_status=error` and a non-successful worker job status.

Alternative considered: raise exceptions to Main Agent tools for all MCP failures. Rejected because Router already has a replayable worker job/event/error model and Main Agent should receive compact tool results instead of raw transport failures.

## Risks / Trade-offs

- [Risk] LLM-simulated `plc-test` and `plc-formal` can be plausible but not technically authoritative. -> Mitigation: label metadata as LLM-simulated and keep this capability scoped to integration and future subagent replacement, not final verification claims.
- [Risk] Model output may be malformed or omit required artifacts. -> Mitigation: validate draft output, require worker-specific artifact writes, and normalize failures instead of mutating TaskState as success.
- [Risk] Bounded artifact snapshots may omit context needed by the LLM. -> Mitigation: include summaries plus configurable content limits, and let workers request clarification or return retryable errors when input is insufficient.
- [Risk] Live DeepSeek calls can spend credits or hang. -> Mitigation: keep automated tests deterministic with fake clients, use short timeouts, and make live smoke scripts opt-in.
- [Risk] Hybrid routing can make local behavior confusing. -> Mitigation: include selected worker route and MCP request ID in worker events, worker job metadata, and script output.
- [Risk] Secrets could leak through diagnostics. -> Mitigation: never print API keys, redact provider configuration, and avoid storing raw request headers in events or artifacts.

## Migration Plan

No database or public API migration is required.

Implementation can land in layers:

1. Add settings and config tests for MCP URL/timeout, per-worker mode, bounded artifact content, and DeepSeek worker-simulation settings.
2. Add draft output models and validation helpers.
3. Add MCP client support with fake-session unit tests.
4. Add LLM-backed MCP server modules with injectable fake DeepSeek clients for tests.
5. Extend `McpAdapter` to route mock/real/hybrid workers and persist draft artifact writes.
6. Add integration tests for MCP contract and the four worker flows.
7. Add opt-in local smoke scripts and documentation.

Rollback can disable the new path by setting `MCP_MODE=mock`; the existing mock worker path remains the default.

## Open Questions

- Should the MCP server live under `backend/app/mcp/` with a module entrypoint, under `scripts/`, or both?
- Should live smoke scripts require an explicit flag such as `--live` even when DeepSeek credentials are present?
- What default artifact content limit is acceptable for local LLM simulation without making prompts too large?
