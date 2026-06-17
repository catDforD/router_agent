## Context

The backend currently has Router v1 Pydantic models, JSON Schema exports, persistence repositories, a local artifact store, event streaming, task creation, and Scheduler Guard checks. `gate_results` persistence already exists, and `TaskState` already contains `gates.can_finish_as_success` plus a `current_artifacts.latest_gate_report` pointer, but `backend/app/services/quality_gate.py` is still empty.

The recommended development order places Quality Gate before Mock MCP, WorkerResult Handler, Agent Tools, and Runtime Loop. That means the first implementation must work against manually constructed or fixture-backed `TaskState` objects rather than depending on a complete worker execution pipeline.

## Goals / Non-Goals

**Goals:**
- Provide a deterministic final-delivery assessment for the current `TaskState`.
- Make the assessment easy to unit test without database, artifact, or event side effects.
- Persist each gate run through a `gate_report` artifact and `gate_results` records.
- Emit observable gate lifecycle events for frontend timelines and replay.
- Set `gates.can_finish_as_success` only when the final gate passes.
- Extend Scheduler Guard so `succeeded` finish requires a passing Quality Gate marker.

**Non-Goals:**
- Do not implement MCP workers, WorkerResult Handler, Agent Tools, Runtime Loop, or final report generation.
- Do not introduce a public `GateResult` Router v1 schema; gate results remain internal persistence rows.
- Do not parse full artifact contents for semantic correctness in the MVP.
- Do not enforce report recency against patch timestamps in the first implementation; rely on `GateState` flags maintained by later WorkerResult Handler logic.

## Decisions

### Split assessment from side effects

Implement a pure assessment function that accepts a `TaskState` and returns a structured gate report object. A service method can wrap that function to write artifacts, gate result rows, events, and the updated task state.

Alternative considered: make `run_quality_gate` directly perform all checks and writes. That would make early unit tests harder and would tie gate behavior to repositories before the worker pipeline exists.

### Treat Quality Gate as an audit boundary, not a scheduler

Quality Gate should read the state produced by Runtime and WorkerResult Handler. It should not choose workers, repair code, resolve failures, or infer new difficulty classifications. Scheduler Guard remains responsible for pre-action validation, while Quality Gate owns the final delivery report.

Alternative considered: fold Quality Gate into Scheduler Guard. That would lose the persisted audit trail required by `gate_report` and `gate_results`.

### Use existing Router v1 fields and artifact types

Use `ArtifactType.GATE_REPORT`, `current_artifacts.latest_gate_report`, `GateState.can_finish_as_success`, `EventType.GATE_STARTED`, `EventType.GATE_PASSED`, and `EventType.GATE_FAILED`. No schema migration is needed for the MVP.

Alternative considered: add a first-class external `GateResult` schema. The current database already supports internal gate results, and externalizing this contract would create unnecessary schema synchronization work.

### Persist one aggregate report and per-gate records

Each run should write one user-visible `gate_report` artifact containing an aggregate JSON/Markdown-style summary. It should also write internal `gate_results` records for `requirements_gate`, `code_gate`, `test_gate`, `formal_gate`, `regression_gate`, and `final_gate`.

Alternative considered: persist only the aggregate result. Per-gate records make replay and debugging easier, especially when E2E tests later need to assert why a task could not finish.

### Keep MVP evidence checks state-based

The MVP should use `TaskState.current_artifacts`, `TaskState.gates`, open clarification questions, active worker jobs, and open failures as the source of truth. It should not read large artifact contents or inspect test/formal report bodies.

Alternative considered: validate report contents immediately. That belongs after WorkerResult Handler and real worker integration establish stable report metadata.

## Risks / Trade-offs

- [Risk] Scheduler Guard and Quality Gate rules can drift over time. -> Mitigation: share stable condition names or helper functions where practical, and add tests for overlapping success-blocking cases.
- [Risk] A stale test or formal report could satisfy MVP checks after a later repair. -> Mitigation: rely on `regression_required` and `formal_regression_required` for now, then add artifact lineage checks after WorkerResult Handler records patch/report relationships.
- [Risk] `can_finish_as_success` can become stale if state changes after a passing gate. -> Mitigation: Quality Gate sets it based on the latest run, and any later state mutation that changes artifacts, failures, worker jobs, or gate requirements should clear it in the owning service.
- [Risk] Writing artifacts and gate results can partially succeed if a later step fails. -> Mitigation: keep all database writes in the caller's session transaction and write the artifact content before metadata cleanup paths already used by Artifact Store.

## Migration Plan

1. Add Quality Gate assessment models/helpers inside `backend/app/services/quality_gate.py`.
2. Add service wiring that uses existing task, artifact, event, and gate repositories.
3. Extend Scheduler Guard finish validation to require `gates.can_finish_as_success` for `succeeded`.
4. Add unit tests against in-memory SQLite and direct fixture-based assessment.
5. Add `scripts/dev_run_gate.py` for manual fixture verification.

Rollback is straightforward because no database migration or external schema change is required. Reverting the service and guard changes restores the previous behavior.

## Open Questions

- Should the aggregate `gate_report` artifact be JSON, Markdown, or JSON with a short Markdown summary? The MVP should prefer JSON for testability unless the frontend needs immediate Markdown rendering.
- Should failed Quality Gate runs clear `latest_gate_report` or point it at the failed report? Pointing at the latest report is more useful for debugging, even when failed.
- When WorkerResult Handler lands, which mutations should always clear `can_finish_as_success`? Likely new worker results, new user messages, new failures, new patches, and gate requirement changes.
