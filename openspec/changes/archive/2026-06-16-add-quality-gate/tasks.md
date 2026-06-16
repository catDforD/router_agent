## 1. Assessment Model

- [x] 1.1 Define internal Quality Gate result dataclasses or Pydantic models for per-gate outcomes, aggregate status, blocking flag, messages, and evidence artifact IDs.
- [x] 1.2 Implement a pure `assess_quality_gate(state)` path in `backend/app/services/quality_gate.py` that returns outcomes for `requirements_gate`, `code_gate`, `test_gate`, `formal_gate`, `regression_gate`, and `final_gate`.
- [x] 1.3 Add assessment logic that allows L0/L1 `qa` tasks to pass without test or formal evidence when no blocking clarification or failure is open.
- [x] 1.4 Add assessment logic that blocks L2+ development or `test_required` tasks when latest passing test evidence is missing.
- [x] 1.5 Add assessment logic that blocks L3+ or formal-required safety-critical tasks when latest passing formal evidence is missing.
- [x] 1.6 Add assessment logic that blocks open required clarification, active worker jobs, open blocking failures, `has_blocking_failure`, `regression_required`, and `formal_regression_required`.

## 2. Persistence Service

- [x] 2.1 Implement `QualityGateService` wiring for task repository, artifact store, event service, and gate result repository.
- [x] 2.2 Write a `gate.started` event before running a persisted gate assessment.
- [x] 2.3 Write a user-visible `gate_report` artifact containing the aggregate result, per-gate outcomes, messages, and evidence artifact IDs.
- [x] 2.4 Persist gate result records for each evaluated gate using existing `GateResultRepository`.
- [x] 2.5 Update `TaskState.gates.can_finish_as_success` to true only when the aggregate assessment passes, otherwise false.
- [x] 2.6 Ensure `TaskState.current_artifacts.latest_gate_report` points at the newest gate report artifact after both passing and failing runs.
- [x] 2.7 Emit `gate.passed` or `gate.failed` with correlation to the gate report artifact.

## 3. Scheduler Guard Integration

- [x] 3.1 Add a Scheduler Guard violation code for missing passing Quality Gate marker.
- [x] 3.2 Update `validate_finish_task(state, "succeeded")` to reject when `state.gates.can_finish_as_success` is not true.
- [x] 3.3 Keep non-success terminal statuses compatible with unresolved gate failures.

## 4. Tests

- [x] 4.1 Add unit tests for pure Quality Gate assessment returning all six gate outcomes.
- [x] 4.2 Test that an L1 `qa` task can pass without test or formal reports.
- [x] 4.3 Test that an L2 `new_plc_development` task without passing test evidence fails `test_gate`.
- [x] 4.4 Test that an L3 or formal-required safety-critical task without passing formal evidence fails `formal_gate`.
- [x] 4.5 Test that open blocking failures fail `final_gate`.
- [x] 4.6 Test that pending regression flags fail `regression_gate`.
- [x] 4.7 Test that a persisted passing run writes a gate report artifact, gate result records, gate events, and sets `can_finish_as_success=true`.
- [x] 4.8 Test that a persisted failing run writes a gate report artifact, gate result records, gate events, and sets `can_finish_as_success=false`.
- [x] 4.9 Extend Scheduler Guard tests so `succeeded` finish is rejected without `can_finish_as_success=true` and allowed when all success conditions including the gate marker are satisfied.

## 5. Developer Verification

- [x] 5.1 Add `scripts/dev_run_gate.py` to run Quality Gate against a fixture task state and print aggregate JSON output.
- [x] 5.2 Add or reuse fixture coverage for an L3 task missing formal evidence.
- [x] 5.3 Verify `uv run pytest backend/app/tests/unit/test_quality_gate.py -q`.
- [x] 5.4 Verify `uv run pytest backend/app/tests/unit/test_scheduler_guard.py -q`.
- [x] 5.5 Verify `uv run python scripts/dev_run_gate.py --fixture task_l3_no_formal.json` reports a blocking `formal_gate` failure.
