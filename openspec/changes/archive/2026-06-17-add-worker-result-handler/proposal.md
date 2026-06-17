## Why

The Router backend can now obtain validated `WorkerResult` payloads from the mock MCP adapter, but those results are not yet projected back into `TaskState`. Without a handler, the runtime cannot reliably know the current code artifact, latest test/formal evidence, open failures, repair rounds, or pending regression obligations after worker execution.

## What Changes

- Add WorkerResult handling that validates a result belongs to the persisted task/job and updates `TaskState` after worker completion.
- Project produced artifact references into the task's current artifact pointers without duplicating artifact persistence responsibilities.
- Merge worker failures, assumptions, and clarification requests into task state with idempotent behavior for replay or retry paths.
- Update gate flags for test pass/fail, formal pass/fail, repair success, blocking failures, and regression requirements.
- Track completed worker jobs and clear active worker references when a result is handled.
- Add focused unit coverage and a local chain runner for mock worker scenarios such as `formal_failed_then_repair_pass`.

## Capabilities

### New Capabilities
- `worker-result-handler`: Applies Router v1 `WorkerResult` payloads to persisted `TaskState` so runtime state reflects current artifacts, evidence, failures, repair rounds, clarification needs, and gate flags.

### Modified Capabilities

None.

## Impact

- Affected code: `backend/app/workers/worker_result_handler.py`, unit tests under `backend/app/tests/unit/`, and a local development script under `scripts/`.
- Uses existing Router v1 models, task/worker repositories, artifact references, mock MCP adapter outputs, and Quality Gate semantics.
- No public API, schema, migration, or dependency changes are expected.
