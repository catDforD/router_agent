## 1. Guard Foundation

- [x] 1.1 Implement `backend/app/services/scheduler_guard.py` with pure validation functions and no repository, event, MCP, or artifact-store side effects.
- [x] 1.2 Add `SchedulerGuardViolation` with stable internal violation codes, message, and optional details.
- [x] 1.3 Add helper predicates for terminal task status, intake-not-classified state, open required clarification, open blocking failure, artifact type lookup, and current artifact matching.

## 2. Worker Dispatch Rules

- [x] 2.1 Implement `validate_worker_call(state, worker_type, input_artifacts)` with generic checks for terminal tasks, waiting-user tasks, unclassified intake tasks, active concurrency, and worker-call budget.
- [x] 2.2 Add `plc-test` and `plc-formal` checks requiring current code, requirements artifact, and input artifact references that match the current task artifacts.
- [x] 2.3 Add `validate_repair_allowed(state, input_artifacts)` for current code, open blocking failure, failure evidence artifacts, and repair-round limit.
- [x] 2.4 Route `plc-repair` through repair-specific validation from `validate_worker_call`.

## 3. Parallel Dispatch Rules

- [x] 3.1 Implement `validate_parallel_jobs(state, jobs)` with active worker plus proposed job concurrency enforcement.
- [x] 3.2 Enforce worker-call budget across the whole proposed batch.
- [x] 3.3 Reject the whole batch when any member violates worker dispatch rules.
- [x] 3.4 Reject parallel batches containing `plc-repair` for v1.

## 4. Finish Rules

- [x] 4.1 Implement `validate_finish_task(state, final_status)` so non-`succeeded` statuses are not blocked by success-only quality evidence rules.
- [x] 4.2 Reject `succeeded` when blocking failures, required tests, required formal verification, required regression, required formal regression, or required clarification remain unresolved.
- [x] 4.3 Keep finish validation compatible with the later Quality Gate by using existing `GateState` fields and not writing gate results.

## 5. Tests and Dev Check

- [x] 5.1 Add `backend/app/tests/unit/test_scheduler_guard.py` covering test-before-dev, formal-before-dev, repair-before-failure, fourth repair, blocking-failure success, and L3 formal skip rejection.
- [x] 5.2 Add unit coverage for intake dispatch rejection, waiting-user dispatch rejection, worker-call limit rejection, parallel limit rejection, parallel invalid-member rejection, parallel repair rejection, regression finish rejection, and no-mutation behavior.
- [x] 5.3 Add `scripts/dev_guard_check.py` that prints the documented PASS lines for invalid test, repair, and finish scenarios.
- [x] 5.4 Run `uv run python -m compileall backend`, `uv run pytest backend/app/tests/unit/test_scheduler_guard.py -q`, and `git diff --check`.
