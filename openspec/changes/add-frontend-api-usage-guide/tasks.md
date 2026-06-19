## 1. Source Verification

- [x] 1.1 Confirm the currently registered FastAPI public paths for health, task lifecycle, events, artifacts, and trace summary.
- [x] 1.2 Review `backend/app/api/tasks.py`, `backend/app/api/events.py`, and `backend/app/api/artifacts.py` for request models, response models, status codes, and SSE behavior.
- [x] 1.3 Review `schema/ts/router_contract.d.ts` and exported JSON Schema file names for type-reference links.

## 2. Frontend API Guide

- [x] 2.1 Create the frontend API usage guide under `docs/` using workflow-first organization.
- [x] 2.2 Document local base URL, startup assumptions, health endpoints, and frontend-only API boundary.
- [x] 2.3 Document the task lifecycle workflow with examples for task creation, task state reads, user message appends, and cancellation.
- [x] 2.4 Document the SSE event stream contract, including frame shape, event data payloads, reconnect cursors, heartbeats, and visibility filtering.
- [x] 2.5 Document frontend task state usage, including common rendering fields and large-content artifact boundaries.
- [x] 2.6 Document artifact listing and artifact content retrieval, including metadata-only lists, UTF-8 content reads, response fields, and artifact panel rendering guidance.
- [x] 2.7 Document final report discovery and rendering through `TaskState.current_artifacts.final_report` or task artifact metadata.
- [x] 2.8 Document `GET /api/tasks/{task_id}/trace` as a compact timeline/debug projection and explain what it intentionally omits.
- [x] 2.9 Document frontend-relevant error handling for validation, not found, conflict, unsupported content, and artifact read failures.
- [x] 2.10 Document Router v1 TypeScript and JSON Schema references without duplicating complete schema definitions.

## 3. Cross-References

- [x] 3.1 Add or update existing documentation links so frontend developers can discover the new guide from related backend or architecture docs.
- [x] 3.2 Ensure the guide references existing local development and schema documentation where relevant.

## 4. Verification

- [x] 4.1 Verify the guide's endpoint list against FastAPI OpenAPI paths or source routes.
- [x] 4.2 Verify examples and SSE details against existing API tests for task, event, artifact, and trace behavior.
- [x] 4.3 Run documentation-safe checks such as `git diff --check`.
