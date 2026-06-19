## 1. Regression Coverage

- [x] 1.1 Add focused integration coverage for cancelling before scheduled Runtime start and confirming no Main Agent run or worker job is created.
- [x] 1.2 Add coverage for cancelling a task with active worker refs and confirming cancellation clears active task worker state while preserving worker job audit rows.
- [x] 1.3 Add coverage for a late worker result after cancellation proving worker job/event audit is preserved and `TaskState` business fields are not projected.
- [x] 1.4 Add or consolidate coverage for timeout, MCP connection, invalid worker output, and worker execution exceptions producing canonical `WorkerResult.error`, `worker_jobs.status`, and worker events.
- [x] 1.5 Add coverage for worker-call budget exhaustion proving Scheduler Guard rejects before dispatch and no worker job, worker event, or worker artifact is created.
- [x] 1.6 Add coverage for Main Agent max-turn exhaustion proving the task is not marked `succeeded` and becomes terminal failed unless already terminal.

## 2. Cancellation State Hardening

- [x] 2.1 Update task cancellation so accepted cancellation clears `TaskState.active_worker_jobs`.
- [x] 2.2 Update task cancellation so accepted cancellation resets `runtime_limits.active_parallel_workers` to `0` without changing worker job audit rows.
- [x] 2.3 Preserve existing idempotent cancellation behavior for already-cancelled tasks and conflict behavior for completed non-cancelled terminal tasks.
- [x] 2.4 Verify Runtime start/resume and Main Agent tool paths continue to reject cancelled terminal tasks without creating new worker jobs.

## 3. Late Worker Result Handling

- [x] 3.1 Update `WorkerResultHandler` to return a no-op result when the target task is already terminal.
- [x] 3.2 Ensure terminal-task no-op handling does not mutate current artifacts, gates, failures, assumptions, unresolved questions, completed worker IDs, phase, or status.
- [x] 3.3 Ensure `McpAdapter` still completes worker job rows and emits terminal worker events before any terminal-task no-op projection decision.

## 4. Error Normalization and Guard Observability

- [x] 4.1 Confirm timeout normalization consistently uses `MCP_TIMEOUT`, `execution_status: "timeout"`, retryable error details, `worker_jobs.status: "timeout"`, and `worker.timeout`.
- [x] 4.2 Confirm connection, schema-invalid, and execution exceptions consistently map to `MCP_CONNECTION_ERROR`, `WORKER_SCHEMA_INVALID`, and `WORKER_EXECUTION_ERROR` with `worker_jobs.status: "error"` and `worker.error`.
- [x] 4.3 Confirm Scheduler Guard budget exhaustion remains a tool-level rejection with violation details and does not create synthetic worker results or worker jobs.
- [x] 4.4 Add or adjust event assertions so normalized errors can be located through events and worker_jobs by task ID and worker job ID.

## 5. Main Agent Control-Plane Failure Handling

- [x] 5.1 Update Main Agent max-turn error handling to record `MAIN_AGENT_MAX_TURNS_EXCEEDED` as a visible error event.
- [x] 5.2 Terminalize non-terminal tasks as `failed` when max-turn exhaustion prevents reliable continuation.
- [x] 5.3 Preserve existing terminal task status when a stale Main Agent error is observed after cancellation or completion.
- [x] 5.4 Ensure Runtime releases or marks idle the episode lease after terminalized Main Agent control-plane failure.

## 6. Verification

- [x] 6.1 Run `uv run pytest backend/app/tests/integration/test_timeout_cancel_error.py -q` or the final focused integration target added by this change.
- [x] 6.2 Run `uv run pytest backend/app/tests/unit/test_task_service.py backend/app/tests/unit/test_task_api.py backend/app/tests/unit/test_worker_result_handler.py -q`.
- [x] 6.3 Run `uv run pytest backend/app/tests/unit/test_mcp_adapter_mock.py backend/app/tests/unit/test_mcp_client.py backend/app/tests/unit/test_scheduler_guard.py backend/app/tests/unit/test_agent_tools.py -q`.
- [x] 6.4 Run `uv run pytest backend/app/tests/unit/test_main_agent_service.py backend/app/tests/unit/test_runtime_service.py backend/app/tests/integration/test_runtime_loop.py -q`.
- [x] 6.5 Run `uv run python -m compileall backend`.
- [x] 6.6 Run `git diff --check`.
