## Why

The backend already persists trace-related IDs across task state, Main Agent events, worker jobs, artifacts, and MCP requests, but those IDs are not yet exposed as one coherent trace map and some runtime events do not carry the full correlation context. This blocks reliable debugging when a task fails because developers must manually join events, worker job rows, artifact rows, and logs.

## What Changes

- Add a Router-internal trace mapping capability that can summarize a task's Main Agent runs, worker jobs, MCP requests, artifacts, quality gate results, and correlated events.
- Add a read-only task trace summary API so frontend and developer tooling can inspect the execution graph without embedding large artifact contents.
- Fill existing event correlation fields consistently for worker, artifact, gate, cancellation, and task lifecycle events where the current task trace context is known.
- Preserve Router trace mapping when external OpenAI SDK trace export is disabled or unavailable.
- Add correlated runtime logging that includes bounded diagnostic context such as `task_id`, `openai_trace_id`, `main_agent_run_id`, `worker_job_id`, and `mcp_request_id` without logging secrets or large artifact contents.
- Add integration coverage for trace map reconstruction across the mock Main Agent path and real/hybrid MCP request-id paths.

## Capabilities

### New Capabilities

- `router-trace-mapping`: Provides a durable, task-scoped trace summary that maps Main Agent runs, worker jobs, MCP requests, artifacts, quality gate results, and Router events through existing IDs.
- `correlated-runtime-logging`: Adds structured runtime log context for task execution, Main Agent episodes, worker dispatch, MCP calls, and trace-summary failures without leaking secrets or large content.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `backend/app/api/tasks.py`
  - `backend/app/services/task_service.py`
  - `backend/app/services/event_service.py`
  - `backend/app/services/quality_gate.py`
  - `backend/app/mcp/adapter.py`
  - `backend/app/core/logging.py`
  - repositories for events, artifacts, worker jobs, and gate results as needed for read-only trace projection
  - tests under `backend/app/tests/unit/` and `backend/app/tests/integration/`
- Public API impact:
  - Adds a read-only task trace summary endpoint.
  - Existing task, event, artifact, worker, and schema contracts remain backward-compatible.
- Database impact:
  - No new table is required for the first implementation.
  - Existing row projections and JSON payloads should be used to reconstruct the trace map by `task_id`.
- Operational impact:
  - Logs become easier to correlate with persisted Router state while continuing to redact secrets and avoid large payloads.
