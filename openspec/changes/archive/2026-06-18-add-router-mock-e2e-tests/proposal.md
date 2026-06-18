## Why

The backend has unit and integration coverage for Runtime, Main Agent tools, mock MCP workers, WorkerResult handling, and Quality Gate, but it does not yet have a Router-level mock end-to-end test suite for the scenario matrix described in `docs/backend.md` step 17. This leaves regressions in cross-boundary persistence, event ordering, artifact projection, worker job counts, gate records, and terminal task status harder to catch before real MCP workers are introduced.

## What Changes

- Add a deterministic mock E2E test suite for Router backend scenarios that starts from task creation and verifies the persisted final state.
- Use fake/scripted Main Agent runner decisions so E2E tests validate Router state machine behavior without requiring live LLM calls or OpenAI credentials.
- Cover success, repair, formal regression, clarification, and exhausted-repair outcomes with assertions over tasks, worker jobs, artifacts, events, gate results, and repair counters.
- Add a local E2E smoke script for running one mock scenario manually against the configured local database and artifact store.
- Keep existing public APIs, Router v1 schemas, database tables, and production Main Agent runner behavior unchanged.

## Capabilities

### New Capabilities
- `router-mock-e2e-tests`: Defines deterministic mock end-to-end scenario coverage for Router runtime orchestration, persistence, artifacts, events, worker jobs, gate results, and terminal task outcomes.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `backend/app/tests/e2e/test_router_mock_scenarios.py`
  - shared test helpers for fake Main Agent runners, if useful
  - `scripts/e2e_run_mock_task.py`
  - optional mock scenario support for exhausted repair attempts
- No public HTTP API changes.
- No Router v1 schema, JSON Schema, TypeScript declaration, or database migration changes.
- No live OpenAI or real MCP worker dependency for the new automated E2E tests.
