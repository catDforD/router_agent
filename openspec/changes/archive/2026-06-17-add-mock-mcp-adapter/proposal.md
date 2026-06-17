## Why

The backend has durable task state, artifacts, events, worker jobs, Scheduler Guard, and Quality Gate, but the MCP worker boundary is still empty. A deterministic mock MCP adapter is needed now so the Router can validate the worker protocol and audit trail before any real PLC worker or Main Agent runtime loop is introduced.

## What Changes

- Add a mock MCP adapter capability for invoking `plc-dev`, `plc-test`, `plc-formal`, and `plc-repair` using existing Router v1 `WorkerInput` and `WorkerResult` contracts.
- Add deterministic mock worker scenarios for successful development/testing, test failure followed by repair, formal failure followed by repair, clarification requests, and timeout normalization.
- Persist mock-produced artifact content through the existing Artifact Store, then return only `ArtifactRef` entries in `WorkerResult.produced_artifacts`.
- Record worker job lifecycle and user-visible worker/artifact events so frontend SSE can observe mock worker execution.
- Normalize timeout, schema-invalid, and execution-error cases into standard `WorkerResult` payloads without contacting real MCP servers.
- Add focused unit coverage and a development script for direct mock worker calls.
- No breaking changes to existing Router v1 schema fields.

## Capabilities

### New Capabilities
- `mock-mcp-adapter`: Defines the backend contract for mock MCP worker calls, mock scenarios, artifact persistence, WorkerResult normalization, worker job records, and worker/artifact events.

### Modified Capabilities

None.

## Impact

- Affected code: `backend/app/mcp/adapter.py`, `backend/app/mcp/mock_worker.py`, `backend/app/mcp/normalizer.py`, `backend/app/core/config.py`, and focused tests under `backend/app/tests/unit/`.
- Affected scripts: add `scripts/dev_call_mock_worker.py` for local manual verification.
- Uses existing repositories and services: `WorkerJobRepository`, `ArtifactStore`, `EventService`, and Router v1 models.
- Does not add real MCP network dependencies; `backend/app/mcp/client.py` remains reserved for the later real MCP integration change.
