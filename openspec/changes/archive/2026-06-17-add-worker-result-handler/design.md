## Context

The backend already has strict Router v1 Pydantic models, persistence repositories, a local Artifact Store, Event Service, Scheduler Guard, Quality Gate, and a mock MCP adapter. The mock adapter can accept `WorkerInput`, persist produced artifact content, complete `worker_jobs`, emit worker/artifact events, and return validated `WorkerResult` payloads.

The missing runtime boundary is the semantic projection from `WorkerResult` back into persisted `TaskState`. Today artifacts may update `TaskState.current_artifacts` through the Artifact Store's type-based pointer mapping, but worker outcomes do not update gate flags, open failures, clarification questions, repair rounds, active/completed job references, or regression obligations. This means later tools and Quality Gate cannot rely on the task state after worker execution.

## Goals / Non-Goals

**Goals:**
- Provide a deterministic handler for applying a Router v1 `WorkerResult` to a persisted task.
- Keep artifact content persistence and worker lifecycle persistence owned by the MCP adapter and existing repositories.
- Make result handling idempotent so replaying or retrying the same terminal worker result does not duplicate failures or increment counters twice.
- Keep Quality Gate and Scheduler Guard semantics coherent by updating pass/fail markers, blocking failure markers, and regression flags in `TaskState`.
- Provide focused unit coverage and a local mock-chain runner that proves dev/test/formal/repair state transitions.

**Non-Goals:**
- Do not implement `worker_input_builder.py`, Main Agent function tools, the runtime loop, or real MCP worker clients.
- Do not introduce a new database table, public API, Router v1 schema field, or migration.
- Do not mark repaired failures as resolved merely because a repair worker produced a patch; validation workers must prove resolution.
- Do not move worker job completion or worker event emission out of the existing adapter in this change.

## Decisions

### Model the handler as a state reducer service

Implement `backend/app/workers/worker_result_handler.py` around a small service/function such as `WorkerResultHandler.handle_worker_result(result)`. The handler should load the persisted task, optionally load the persisted worker job for identity/context checks, compute an updated `TaskState`, persist it through `TaskRepository`, and return a compact result object with the updated task and applied summary.

Alternative considered: fold state updates into `McpAdapter.call_worker`. Rejected because real worker clients, agent tools, replay scripts, and future runtime loops should all share one result application path independent of how a result was obtained.

### Treat adapter output as facts, handler output as task semantics

The adapter remains responsible for artifact content writes, worker job completion, and worker/artifact lifecycle events. The handler should validate that `WorkerResult.produced_artifacts` reference existing task artifacts and then project those references into semantic task state. It may update `current_artifacts` pointers idempotently, but it should not write artifact content or complete jobs again.

Alternative considered: make the handler write artifacts from the result. Rejected because `WorkerResult.produced_artifacts` contains `ArtifactRef` values, not artifact content, and the adapter already guarantees artifact persistence before returning a result.

### Derive gate state from worker type and outcome

The handler should use both `execution_status` and `outcome.status`. Only terminal completed results with passed/failed/need-clarification business outcomes should apply worker-specific semantics.

- `plc-dev` passed: update requirements/code/I/O artifact pointers, clear stale test/formal pass markers because a new code artifact invalidates previous verification evidence, and move toward testing or formal verification based on gate requirements.
- `plc-test` passed: set `latest_test_passed=true`, clear `regression_required`, and resolve open test failures proved by this test run.
- `plc-test` failed: set `latest_test_passed=false`, append open blocking test failures, and set `has_blocking_failure=true`.
- `plc-formal` passed: set `latest_formal_passed=true`, clear `formal_regression_required`, and resolve open formal failures proved by this formal run.
- `plc-formal` failed: set `latest_formal_passed=false`, append open blocking formal failures, preserve counterexample evidence, and set `has_blocking_failure=true`.
- `plc-repair` passed: update patch/repair summary/patched code pointers, increment `repair_rounds` exactly once per worker job, set `regression_required=true`, set `formal_regression_required=true` if any open formal failure existed before repair, and invalidate stale pass markers for the repaired code.

Alternative considered: resolve failures immediately after repair. Rejected because a patch is only a candidate fix; regression test/formal workers must prove the blocking issue is resolved.

### Recalculate blocking state after every application

`gates.has_blocking_failure` should be derived from the final failure list after appending or resolving failures. This avoids stale flags when test/formal regression passes resolve the last open blocking failure. `gates.can_finish_as_success` should be cleared after worker result handling because Quality Gate must be rerun against the new state.

Alternative considered: update only the flag mentioned by the current worker branch. Rejected because independent failures can remain open after one source passes, and Quality Gate depends on an aggregate blocking marker.

### Make replay safe

The handler should consider a worker result already applied when its `worker_job_id` is already present in `TaskState.completed_worker_job_ids`. Reapplying should return the current task or a no-op result without mutating counters or duplicating list entries. Failure, assumption, artifact, and clarification merges should dedupe by stable IDs.

Alternative considered: raise on duplicate handling. Rejected because replay and recovery workflows are part of the planned runtime model, and no-op idempotency is easier to compose.

## Risks / Trade-offs

- [Risk] Artifact Store and handler both know artifact-type pointer mappings. -> Mitigation: keep pointer projection small and type-based, and consider extracting the mapping to a shared helper if duplication appears during implementation.
- [Risk] Repair failure resolution semantics may be too conservative for demos. -> Mitigation: require validation worker pass to resolve failures; this matches the Quality Gate and Scheduler Guard safety model.
- [Risk] A worker may return a result with artifact refs that were not persisted for the task. -> Mitigation: validate produced artifact IDs against `ArtifactRepository` before applying semantic state changes.
- [Risk] Phase transitions can become policy-heavy. -> Mitigation: keep phase updates conservative in this change and let the future runtime loop choose the next action from state and `next_recommended_action`.

## Migration Plan

No schema migration is required. The change adds a new handler module, tests, and a development script. Existing mock adapter tests should continue to pass because adapter responsibilities remain unchanged.

## Open Questions

- Should handler emit a `task.updated` event after applying a result, or should the future agent tool/runtime layer own that event? The initial implementation can avoid new event emission unless tests or frontend needs require it, because worker events already exist.
- Should resolved failures retain explicit `resolved_by_worker_job_id` pointing at the passing validator job or the repair job? The safer default is the passing validator job, because it proves the fix.
