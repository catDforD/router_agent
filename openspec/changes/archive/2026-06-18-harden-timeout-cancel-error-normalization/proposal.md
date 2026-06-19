## Why

Cancellation, worker timeout/error normalization, guard budget exhaustion, and Main Agent max-turn failures are implemented across several runtime layers, but their combined reliability semantics are not yet captured as one apply-ready contract. This change hardens those paths so tasks do not hang indefinitely, cancelled tasks are not mutated by stale work, and every failure remains diagnosable through events and worker jobs.

## What Changes

- Define Router's v1 soft-cancellation policy for background Runtime episodes and in-flight worker results.
- Ensure late worker results after task cancellation remain auditable but do not project business artifacts, gates, failures, or final status onto a terminal cancelled task.
- Normalize worker timeout, MCP connection, worker schema, and execution errors into consistent `WorkerResult.error`, `worker_jobs.status`, and worker events.
- Define terminal or resumable behavior for Main Agent max-turn failures and worker-call budget exhaustion so tasks do not remain indefinitely running.
- Add focused integration coverage for cancellation, timeout, normalized errors, guard budget exhaustion, and Main Agent max-turn failure.
- No public API, database schema, Router v1 schema, JSON Schema, or TypeScript contract changes are intended.

## Capabilities

### New Capabilities
- `runtime-failure-control`: Defines cross-layer reliability behavior for cancellation, timeout, normalized worker errors, late worker results, guard budget exhaustion, and Main Agent max-turn failure.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `backend/app/services/task_service.py`
  - `backend/app/services/runtime_service.py`
  - `backend/app/agents/main_agent.py`
  - `backend/app/agents/tools.py`
  - `backend/app/mcp/adapter.py`
  - `backend/app/mcp/normalizer.py`
  - `backend/app/workers/worker_result_handler.py`
  - focused tests under `backend/app/tests/unit/` and `backend/app/tests/integration/`
- Existing `POST /api/tasks/{task_id}/cancel` remains the cancellation entry point.
- Existing `WorkerResult`, `RouterEvent`, `TaskState`, and `WorkerJob` contract shapes remain unchanged.
