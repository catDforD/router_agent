## Why

Router now has task persistence, artifact storage, event streaming, task creation, and deterministic scheduler guard checks, but final delivery still lacks a persisted, replayable audit step. Quality Gate should become the explicit pre-delivery boundary that proves the current `TaskState` has the required evidence before a task can be marked `succeeded`.

## What Changes

- Add a Quality Gate service that evaluates the current `TaskState` before final delivery.
- Split gate behavior into a pure assessment path for fixture/unit tests and a service path that writes runtime records.
- Produce a `gate_report` artifact summarizing passed and failed gates.
- Persist per-run quality gate outcomes in `gate_results`.
- Emit `gate.started` and `gate.passed` or `gate.failed` events for frontend visibility and replay.
- Update `TaskState.gates.can_finish_as_success` according to the latest final gate result.
- Require successful task completion to be preceded by a passing Quality Gate marker.

## Capabilities

### New Capabilities
- `quality-gate`: Evaluates final delivery readiness, records gate evidence, writes a gate report artifact, emits gate events, and updates success eligibility.

### Modified Capabilities
- `scheduler-guard`: Successful finish validation must require `TaskState.gates.can_finish_as_success` to be true so Runtime cannot mark a task `succeeded` without a passing Quality Gate.

## Impact

- Affected code: `backend/app/services/quality_gate.py`, `backend/app/services/scheduler_guard.py`, repositories already available for tasks, artifacts, events, and gate results.
- Affected tests: add focused unit coverage for Quality Gate and extend successful finish guard coverage.
- Affected development scripts: add a local `scripts/dev_run_gate.py` fixture runner for manual gate verification.
- No Router v1 external schema change is required; `gate_results` remains an internal persistence record and `gate_report` uses the existing artifact type.
