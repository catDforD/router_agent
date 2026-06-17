## Why

Main Agent scheduling rules currently live in docs and prompts, but the runtime has no deterministic action guard before worker jobs or final status changes are accepted. This leaves the next implementation steps vulnerable to invalid worker calls, skipped required verification, excessive repair loops, and premature successful completion.

## What Changes

- Add a Scheduler Guard service that validates worker calls, parallel job batches, repair eligibility, and final task status before Runtime or Agent tools execute those actions.
- Enforce task-state preconditions for PLC workers, including current code requirements, failure evidence requirements, repair limits, worker-call limits, active concurrency limits, and clarification pauses.
- Enforce final-success preconditions that prevent `succeeded` when blocking failures, unresolved required clarification, required tests, required formal verification, or required regression work remain open.
- Keep guard behavior pure and deterministic: it reports violations without mutating task state, writing events, creating worker jobs, or running quality gates.
- Add focused unit tests and a small development check script for illegal scheduling scenarios.

## Capabilities

### New Capabilities

- `scheduler-guard`: Deterministic runtime policy checks for worker dispatch, parallel dispatch, repair eligibility, and task completion.

### Modified Capabilities

- None.

## Impact

- Adds `backend/app/services/scheduler_guard.py` as the shared policy layer for future Runtime and Agent tool entry points.
- Adds unit coverage for guard decisions in `backend/app/tests/unit/test_scheduler_guard.py`.
- Adds `scripts/dev_guard_check.py` for a quick local guard sanity check.
- Does not change Router v1 JSON schema, database schema, public Task API, artifact storage, or event streaming behavior.
