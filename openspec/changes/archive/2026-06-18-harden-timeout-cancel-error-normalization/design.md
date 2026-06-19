## Context

The backend already has the main pieces for cancellation, timeout, and error handling: `POST /api/tasks/{task_id}/cancel`, Runtime terminal-task checks, MCP adapter normalization, worker job persistence, Scheduler Guard limits, and Main Agent max-turn error recording. The remaining gap is the combined reliability contract across those pieces.

The critical edge case is cancellation while work is already in flight. The current Router worker path is synchronous from the Runtime's point of view, and the external MCP worker API does not expose a cancellation endpoint. This change therefore treats cancellation as a Router-side terminal state that prevents future scheduling and business-state projection, while still allowing already-started worker jobs to complete and remain auditable.

## Goals / Non-Goals

**Goals:**

- Make task cancellation terminal and observable across Task API, Runtime, Main Agent tools, and WorkerResult handling.
- Preserve worker job and event audit trails for timeouts, normalized errors, and late in-flight worker completions.
- Prevent late worker results from changing cancelled or otherwise terminal task business state.
- Ensure Main Agent control-plane failures and worker-call budget exhaustion cannot leave tasks indefinitely running without an observable terminal or retryable state.
- Keep existing Router v1 public contract shapes unchanged.

**Non-Goals:**

- Do not add a remote MCP worker cancellation protocol.
- Do not make synchronous worker calls preemptible in-process.
- Do not change Router v1 schema, JSON Schema files, TypeScript declarations, or database tables.
- Do not implement the deferred parallel-worker feature from `docs/backend.md` section 23.
- Do not collapse Scheduler Guard rejections into `WorkerResult.error`; guard rejections happen before worker dispatch.

## Decisions

### Use soft cancellation as the v1 cancellation model

When a user cancels a task, Router marks the task `cancelled`, moves it to `completed`, records `task.cancelled`, clears active worker references/counters from `TaskState`, and rejects subsequent Runtime or tool dispatch for that task.

Already-started worker jobs may still complete because Router has no remote cancellation channel in v1. Their completion remains visible through `worker_jobs` and worker events, but it must not revive or mutate the cancelled task.

Alternative considered: introduce hard cancellation now by adding cancellation tokens or an MCP cancel tool. Rejected for this change because it would require a cross-process worker protocol and would expand the scope beyond reliability hardening.

### Treat late worker results for terminal tasks as audit-only

`WorkerResultHandler` should explicitly no-op when the target task is already terminal. The worker job result and terminal worker event are preserved by `McpAdapter`, but the handler must not update `TaskState.current_artifacts`, gates, failures, assumptions, unresolved questions, completed job IDs, phase, or final status.

Alternative considered: apply the result partially by appending completed job IDs to terminal tasks. Rejected because terminal task state should remain stable after cancellation or final completion; worker_jobs already provides the audit source for late work.

### Keep error normalization centered on WorkerResult only after dispatch

Timeout, connection, schema-invalid, and execution exceptions after worker dispatch become standard `WorkerResult` payloads with `WorkerError`, terminal `worker_jobs.status`, and a visible worker event.

Scheduler Guard rejections occur before dispatch. They should remain tool-level rejections with violation details, no worker job, and no synthetic `WorkerResult`. `GUARD_REJECTED` is a category for observability and documentation, not a worker execution status.

Alternative considered: create synthetic worker results for guard rejections. Rejected because no worker was invoked, and adding fake worker jobs would make replay less accurate.

### Fail fast on unrecoverable Main Agent control-plane exhaustion

`MAIN_AGENT_MAX_TURNS_EXCEEDED` is an unrecoverable control-plane failure for the current episode. If the task is still non-terminal, Router should record a visible Main Agent error and move the task to a terminal failed state rather than leaving it running for repeated Runtime retries.

Worker-call budget exhaustion remains a guard rejection at the attempted dispatch site. If the model cannot recover and reaches max turns, the same Main Agent failure path terminates the task.

Alternative considered: leave max-turn failures as non-terminal and wait for another Runtime retry. Rejected because it can repeatedly re-enter the same failure and violates the "task does not hang indefinitely" goal.

### Preserve existing contracts and add focused regression coverage

The change should use existing enums and flexible error-code fields. Tests should prove observable outcomes through persisted `TaskState`, `RouterEvent`, and `worker_jobs` rows instead of introducing new schema fields.

Alternative considered: add a first-class error-code enum to Router v1. Rejected because current contracts intentionally allow string error codes and the required behavior can be expressed without a schema migration.

## Risks / Trade-offs

- [Risk] Soft cancellation may surprise users who expect remote worker interruption. -> Mitigation: document v1 behavior and make late worker completion visibly auditable but non-mutating.
- [Risk] Marking Main Agent max-turn failures as failed can terminate tasks that might succeed on retry. -> Mitigation: reserve the terminal path for explicit max-turn/model-behavior failures and keep ordinary worker timeout results retryable inside one episode.
- [Risk] Clearing active worker references on cancel may hide in-flight work from `TaskState`. -> Mitigation: use `worker_jobs` and events as the authoritative audit trail for worker lifecycle after cancellation.
- [Risk] Error codes can drift because they are strings. -> Mitigation: keep constants centralized in normalizer/tool paths and assert canonical codes in tests.

## Migration Plan

No database, HTTP API, or Router schema migration is required.

Rollout can happen behind existing behavior:

1. Add regression tests that describe the desired cancellation, late-result, normalized-error, guard-budget, and max-turn outcomes.
2. Harden state transitions and handler no-op behavior until those tests pass.
3. Run existing task API, Runtime, Main Agent, MCP adapter, Scheduler Guard, and WorkerResult Handler tests to prove no contract regressions.

Rollback removes the new state-transition hardening and tests; existing public APIs and persisted schema remain compatible.

## Open Questions

- Should Main Agent model-behavior errors other than max turns also terminalize the task immediately, or should some remain retryable?
- Should cancellation record a specific metadata flag listing active worker IDs that were abandoned from `TaskState`, or are worker_jobs/events sufficient for v1?
