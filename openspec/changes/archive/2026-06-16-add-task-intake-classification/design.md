## Context

The Task API now creates an observable task shell: `status="created"`, `phase="intake"`, `task_type="unknown"`, `difficulty.level="L0"`, low confidence, and no test or formal gates. That behavior keeps HTTP task creation fast and deterministic, but it leaves Runtime without enough information to choose the first worker safely.

Router v1 already has the state fields needed for classification:

- `TaskState.normalized_goal`
- `TaskState.task_type`
- `TaskState.difficulty`
- `TaskState.gates`
- `TaskState.unresolved_questions`
- `WorkerInput.context.task_type`
- `WorkerInput.context.difficulty_level`

The missing piece is the transition from conservative bootstrap state to classified intake state. That transition should be driven by the Main Agent, but guarded by deterministic Runtime rules before any worker can run.

```
POST /api/tasks
   |
   v
created/intake/unknown/L0
   |
   v
Runtime starts intake episode
   |
   v
Main Agent classification result
   |
   v
Runtime validation and gate elevation
   |
   +--> waiting_user/clarifying
   |
   +--> running/planning or first worker call
```

## Goals / Non-Goals

**Goals:**

- Add a structured Main Agent intake classification result.
- Apply a validated classification result to the existing `TaskState`.
- Ensure worker execution cannot start from an unclassified `unknown/L0` task except for explicitly allowed QA/no-worker flows.
- Enforce deterministic safety rules for difficulty and gate requirements.
- Make classification observable through existing event types.
- Keep the current Task API behavior unchanged.

**Non-Goals:**

- Do not implement detailed task classification inside `POST /api/tasks`.
- Do not add a keyword-based classifier as the final source of truth.
- Do not introduce new Router v1 enum values, JSON Schema files, TypeScript declarations, or database migrations.
- Do not require real OpenAI Agent execution for unit tests.
- Do not implement full task execution, worker result handling, final reporting, or eval suites in this change.

## Decisions

### Classify during Runtime intake, not synchronous task creation

`TaskService.create_task` should continue to create a conservative, durable task shell. The Runtime loop should run an intake episode after task creation and before the first worker call.

This preserves the existing Task API contract and avoids blocking HTTP task creation on agent latency or model availability.

Alternatives considered:

- Put keyword rules directly in `create_task`: simple, but it creates a temporary policy path that conflicts with the intended agent-driven design.
- Require users to provide task type and difficulty in the API request: shifts responsibility to clients and weakens backend policy control.

### Use an internal structured classification output

Add an internal Pydantic model for the Main Agent's intake output. It should not become a Router v1 cross-service schema unless later external consumers need it.

The output should include:

- `normalized_goal`
- `task_type`
- `difficulty.level`
- `difficulty.score`
- `difficulty.confidence`
- `difficulty.reasons`
- `difficulty.signals`
- `requires_test`
- `requires_formal`
- `requires_repair_loop`
- `need_clarification`
- optional clarification questions

Runtime should map this output onto the existing `TaskState` fields instead of adding new state shape.

### Runtime owns safety gate elevation

The Main Agent can classify and explain, but Runtime must validate and elevate unsafe decisions. Safety-related signals are stronger than the model's selected difficulty level.

Minimum deterministic policy:

- If `difficulty.level` is `L2` or higher, `gates.test_required` must be true.
- If any of these signals are true, the task must be treated as at least `L3` and `gates.formal_required` must be true:
  - `has_safety_constraints`
  - `has_emergency_stop`
  - `has_interlock`
  - `has_fault_latching`
  - `has_mode_switching`
  - `has_state_machine`
- If `task_type="repair_existing_code"`, `difficulty.requires_repair_loop` must be true.
- If `need_clarification=true`, Runtime must not call workers until the task has enough information to proceed.

Runtime may either reject inconsistent classification output or coerce it upward to the safe minimum. For MVP, coercion with an explicit reason is preferable because it preserves forward progress while making policy visible.

### Classification updates are persisted as state transitions

Applying classification should update the current task atomically:

- `normalized_goal`
- `task_type`
- `difficulty`
- `gates`
- `phase`
- `status`
- `unresolved_questions`, when clarification is required
- `updated_at`

Suggested phase/status mapping:

- Classified and ready to plan: `status="running"`, `phase="planning"`
- Needs clarification: `status="waiting_user"`, `phase="clarifying"`
- QA task that can finish without workers: remains available for the Main Agent to finalize through the normal finish path

### Reuse existing event types

Do not add `task.classified` to Router v1 yet. Emit:

- `main_agent.started` when the intake episode begins.
- `main_agent.decision` with an internal summary of the classification.
- `task.updated` when the persisted `TaskState` changes.
- `task.waiting_user` if clarification is required.

This keeps event streams observable without requiring schema, JSON Schema, and TypeScript contract changes.

## Risks / Trade-offs

- [Risk] Coercing agent output can hide model mistakes. -> Mitigation: append explicit difficulty/gate reasons when Runtime elevates a decision and emit the original decision in `main_agent.decision` payload.
- [Risk] A task may be over-classified and require formal verification unnecessarily. -> Mitigation: start with a conservative safety policy; later eval data can tune thresholds or add waiver flows.
- [Risk] Keeping classification output internal may require refactoring if external consumers later need it. -> Mitigation: map internal output only to existing Router v1 fields and avoid leaking internal-only shapes through public APIs.
- [Risk] Real Main Agent output can be nondeterministic. -> Mitigation: unit tests should use fixed classification objects; integration tests can use a mock Main Agent before real model tests are added.

## Migration Plan

No database or Router v1 schema migration is required. Existing tasks with `unknown/L0` remain valid and can be classified when Runtime starts or resumes them.

Rollback removes the intake classification episode and leaves `POST /api/tasks` behavior unchanged.

## Open Questions

- Should Runtime reject inconsistent classifications in strict mode after the MVP, instead of coercing upward?
- Should `task.updated` for classification be user-visible, or should only clarification-required transitions be user-visible?
- Should `qa` tasks move directly to synthesis/finalization, or should they pass through `planning` first for consistency?
