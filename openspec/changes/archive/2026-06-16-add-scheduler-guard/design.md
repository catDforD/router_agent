## Context

Router currently has validated Pydantic models, persistence repositories, local artifact storage, event streaming, Task API flows, and an intake-classification contract. The runtime execution files that will call PLC workers are still mostly empty, so this is the right point to define a deterministic guard layer before worker dispatch and task completion logic spreads across Runtime, Agent tools, and quality gates.

Several invariants already exist in `TaskState`, `RuntimeLimits`, and `WorkerInput` validators. Scheduler Guard should build on those validators instead of replacing them. The guard handles action-level policy before side effects happen; model validators remain the last line of defense against invalid persisted state.

The implementation must respect the existing Router v1 contract and avoid changing JSON Schema, database migrations, public Task API behavior, or event payload formats.

## Goals / Non-Goals

**Goals:**

- Provide a single deterministic policy entry point for worker dispatch, parallel dispatch, repair eligibility, and final status validation.
- Reject illegal Main Agent or Runtime actions before creating worker jobs, writing events, calling MCP tools, or updating task status.
- Keep error output structured enough for Agent tools, API handlers, and tests to identify the violated scheduling rule.
- Make the guard usable before the full Runtime loop, MCP adapter, WorkerResult handler, and Quality Gate are implemented.

**Non-Goals:**

- Do not mutate `TaskState`, increment counters, write events, create worker jobs, or generate artifacts inside Scheduler Guard.
- Do not run Quality Gate or persist gate results. Guard can require a successful gate marker later, but Quality Gate remains the owner of delivery validation reports.
- Do not decide which worker should run next. Main Agent and Runtime planning still own scheduling decisions; the guard only accepts or rejects proposed actions.
- Do not change Router v1 schema definitions or database schema.

## Decisions

### Keep Scheduler Guard Pure

Scheduler Guard will expose pure validation functions that accept a `TaskState` plus proposed action details and either return successfully or raise a structured `SchedulerGuardViolation`.

Alternatives considered:

- Mutating state in guard: rejected because it would blur ownership with WorkerResult Handler and TaskService.
- Returning booleans: rejected because callers need actionable failure codes and messages.

Rationale: Pure functions are easy to unit test, safe to call from multiple entry points, and can be introduced before the runtime loop is complete.

### Use Structured Violation Codes

Guard failures should include a stable code, human-readable message, and optional details. Initial codes should cover terminal task, waiting clarification, intake not classified, missing current code, missing requirements, missing repair evidence, no open blocking failure, repair limit reached, worker call limit reached, parallel limit exceeded, required test missing, required formal missing, regression required, formal regression required, blocking failure present, and required clarification open.

Alternatives considered:

- Reusing generic `ValueError`: rejected because tests and tool responses would depend on brittle message matching.
- Adding new schema enums: rejected for this change because guard errors are internal service behavior, not Router v1 cross-service contract.

Rationale: Stable internal codes make Agent tool behavior and test expectations precise without expanding external contracts.

### Separate Action Preconditions From State Transition Invariants

`validate_worker_call` should reject a proposed worker call before `WorkerInputBuilder` constructs a full request. `WorkerInput` validation still checks final input structure and artifact types. `TaskState` validation still prevents impossible persisted state.

Alternatives considered:

- Rely only on `WorkerInput` and `TaskState` validators: rejected because invalid actions would be discovered too late, after planning code already attempted to build or persist side effects.
- Duplicate all model validators in guard: rejected because it creates drift.

Rationale: Guard provides earlier, clearer errors while the model layer remains authoritative for schema-level invariants.

### Treat Repair As Serialized In V1

`validate_parallel_jobs` should reject batches containing `plc-repair` for v1. Repair changes `current_code`, increments repair rounds, and creates regression obligations, so parallel repair can race with test/formal jobs and make artifact lineage ambiguous.

Alternatives considered:

- Allow repair in parallel with independent modules: deferred until module-scoped artifact lineage exists.
- Allow only one repair job in a parallel batch: rejected for v1 because even one repair job races with parallel validators over which code version is current.

Rationale: Conservative serialization keeps repair loop behavior deterministic and matches the current TaskState model.

### Let WorkerResult Handler Own Regression Flags

When a repair succeeds, WorkerResult Handler must set `gates.regression_required=true` and, if formal previously failed, `gates.formal_regression_required=true`. Scheduler Guard should enforce these flags at finish time but should not set them itself.

Alternatives considered:

- Set regression flags before launching repair: rejected because a failed or cancelled repair should not create the same post-repair obligations as a successful code change.

Rationale: Regression obligations are a consequence of applied repair results, not merely attempted repair dispatch.

### Guard Final Success, Not All Terminal Outcomes Equally

`validate_finish_task` should be strict for `succeeded`. It can allow `failed`, `partial_failed`, or `cancelled` to proceed through their own lifecycle checks elsewhere, because those statuses are valid ways to stop with unresolved failures.

Alternatives considered:

- Require all final statuses to satisfy Quality Gate: rejected because failure and partial-failure completion need to preserve diagnostic state.

Rationale: The primary unsafe outcome is claiming success while required evidence or blocking work remains unresolved.

## Risks / Trade-offs

- Guard and Quality Gate may appear to overlap -> Keep guard focused on pre-action and final-success preconditions; Quality Gate owns report generation and full delivery assessment.
- `requirements_ir` may be missing in early runtime flows -> Guard should make this visible by rejecting test/formal dispatch until intake or dev flows produce the required artifact.
- Future parallel module repair may be constrained by v1 serialization -> Revisit once module scope and artifact lineage can distinguish independent code regions.
- Structured violation codes could become public by accident -> Treat them as internal service codes unless a later API contract explicitly exposes them.

## Migration Plan

No data migration is required. Implement the service and tests first, then wire guard calls into Agent tools and Runtime in later changes. Because the guard is pure and currently has no callers, rollback is removing the new service, tests, and development script without affecting persisted data.

## Open Questions

- Should Quality Gate write `gates.can_finish_as_success=true`, or should `validate_finish_task` infer success eligibility directly from latest reports and flags until Quality Gate exists?
- Should `requirements_ir` be produced by intake classification or by the first dev worker result? This affects when test/formal dispatch becomes legal.
