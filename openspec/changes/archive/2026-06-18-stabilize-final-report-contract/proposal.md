## Why

Final reports already exist as `FINAL_REPORT` artifacts, but the contract is still too loose: current coverage mostly proves that a report artifact is created, not that it contains the stable delivery summary the frontend and audit trail need. The backend plan also still says `finish_task` must generate the report, while the implementation has moved to report-first Runtime finalization.

## What Changes

- Stabilize the `FINAL_REPORT` artifact content contract as a compact Router v1 report object built from persisted task state, artifacts, gate results, failures, assumptions, and the validated Main Agent episode output.
- Require intentional terminal outcomes `succeeded`, `partial_failed`, and `failed` produced through Main Agent/Runtime finalization to persist a user-visible final report before emitting the terminal task event.
- Preserve the existing report-first finalization direction: orchestration returns a final structured output, Runtime writes report artifacts, emits `main_agent.completed`, and only then applies terminal status.
- Keep large generated content externalized; final reports reference PLC code, test reports, formal reports, patches, counterexamples, gate reports, and logs by artifact ID and summary rather than embedding full bodies.
- Add focused tests that assert final report content for success, partial failure, and control-plane failure paths, not only artifact existence.
- Update backend planning documentation so section 26 describes Runtime-owned report-first finalization instead of direct `finish_task` report creation.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `main-agent-turn-observability`: Strengthen final report durability and content requirements across terminal Main Agent/Runtime finalization outcomes.
- `local-artifact-store`: Clarify the persisted `FINAL_REPORT` payload shape, visibility, readability, and artifact-reference behavior.
- `router-mock-e2e-tests`: Extend mock E2E audit assertions to verify final report content, referenced artifact IDs, and terminal event ordering.

## Impact

- Affected code:
  - `backend/app/agents/observability.py`
  - `backend/app/agents/main_agent.py`
  - `backend/app/services/artifact_store.py`
  - `backend/app/services/trace_summary.py`
  - tests under `backend/app/tests/unit/`, `backend/app/tests/integration/`, and `backend/app/tests/e2e/`
  - `docs/backend.md`
- Existing public HTTP endpoints remain unchanged.
- Existing Router v1 enum values remain unchanged: reports continue to use artifact type `final_report`, replay logs continue to use `main_agent_log`.
- No database migration is expected.
- The in-progress `add-openai-compatible-main-agent-runner` change should route compatible provider final outputs through the same final report contract when implemented.
