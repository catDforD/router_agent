## 1. Trace Summary Projection

- [x] 1.1 Add compact trace summary response models or DTOs for task trace identity, Main Agent runs, worker jobs, MCP request IDs, artifacts, gate results, and event references.
- [x] 1.2 Add task-scoped repository helpers for listing worker jobs and gate results when existing repository APIs are insufficient.
- [x] 1.3 Implement a read-only trace summary service that reconstructs the projection from task state, events, worker jobs, artifacts, and gate results by `task_id`.
- [x] 1.4 Ensure trace summary output excludes full artifact content, full logs, raw model outputs, raw SDK events, full MCP payloads, and hidden reasoning.
- [x] 1.5 Add unit tests for deterministic ordering, bounded metadata, missing task behavior, and reconstruction from persisted rows.

## 2. Trace Summary API

- [x] 2.1 Add `GET /api/tasks/{task_id}/trace` as a read-only endpoint backed by the trace summary service.
- [x] 2.2 Map missing tasks to HTTP 404 without creating events, artifacts, worker jobs, gate results, or task mutations.
- [x] 2.3 Add API tests proving the endpoint returns task trace identity, Main Agent run IDs, worker job summaries, artifact summaries, gate summaries, and event summaries for an existing task.
- [x] 2.4 Add API tests proving large artifact and replay log content is not embedded in the trace response.

## 3. Event Correlation Propagation

- [x] 3.1 Update worker lifecycle event creation so `worker.started` and terminal worker events include `openai_trace_id` and `main_agent_run_id` from `WorkerInput.trace_context` when available.
- [x] 3.2 Update worker artifact creation events so `artifact.created` includes worker job ID, MCP request ID, `openai_trace_id`, and Main Agent run ID when available.
- [x] 3.3 Update Quality Gate event creation so `gate.started`, `gate.passed`, and `gate.failed` include the current task trace IDs when available.
- [x] 3.4 Update task lifecycle event helpers used for cancellation and terminal transitions so they include the task's trace IDs when available.
- [x] 3.5 Add unit tests proving worker, artifact, gate, cancel, and terminal task events carry complete correlation without changing Router v1 schema fields.

## 4. External Trace Export Independence

- [x] 4.1 Add integration coverage proving a fake or non-SDK Main Agent run still creates Router trace IDs and appears in the trace summary.
- [x] 4.2 Add integration coverage proving worker inputs and worker events inherit Router trace context even when no external SDK trace export is used.
- [x] 4.3 Add coverage for real or hybrid MCP dispatch proving `mcp_request_id` appears in worker input, worker result, worker events, and the trace summary.

## 5. Correlated Runtime Logging

- [x] 5.1 Add logging helpers for structured context fields such as `task_id`, `openai_trace_id`, `main_agent_run_id`, `worker_job_id`, `worker_type`, `mcp_tool`, and `mcp_request_id`.
- [x] 5.2 Add redaction and bounding behavior for secret-like keys, database credentials, raw model output, MCP payload bodies, and artifact content.
- [x] 5.3 Apply correlated logging at Main Agent episode start/completion/error boundaries.
- [x] 5.4 Apply correlated logging at worker dispatch, worker terminal result, and real MCP request timeout/error boundaries.
- [x] 5.5 Apply error logging around trace summary projection failures without mutating persisted Router state.
- [x] 5.6 Add logging tests proving trace context appears in captured logs while API keys, tokens, database passwords, full code, report bodies, and artifact contents do not.

## 6. Verification

- [x] 6.1 Run focused unit tests for trace summary projection, repository helpers, event correlation propagation, and logging redaction.
- [x] 6.2 Run `uv run pytest backend/app/tests/integration/test_main_agent_with_mock_tools.py -q` or the updated trace-summary integration target.
- [x] 6.3 Run `uv run pytest backend/app/tests/unit/test_event_api.py backend/app/tests/unit/test_artifact_api.py backend/app/tests/unit/test_task_api.py -q`.
- [x] 6.4 Run real or hybrid MCP-focused tests that cover `mcp_request_id` propagation when local dependencies are available.
- [x] 6.5 Run `uv run python -m compileall backend`.
- [x] 6.6 Run `git diff --check`.
