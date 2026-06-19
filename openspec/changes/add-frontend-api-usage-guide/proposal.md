## Why

Frontend integration currently depends on details spread across the backend roadmap, architecture notes, FastAPI source, tests, and Router v1 TypeScript contract. A focused frontend API usage guide is needed now so UI work can consume task lifecycle, SSE events, artifacts, final reports, and trace summaries consistently without reverse-engineering backend internals.

## What Changes

- Add a frontend-facing API usage guide that documents the supported Router backend endpoints, request and response shapes, error behavior, and local usage assumptions.
- Document the recommended frontend task workflow: create task, subscribe to events, render progress, fetch task state, fetch artifacts, handle clarification messages, cancel tasks, and inspect trace summaries.
- Document the SSE contract explicitly, including event stream content type, frame shape, event IDs, `Last-Event-ID`, `after_seq`, heartbeat frames, and frontend-visible event filtering.
- Document artifact consumption rules, including metadata-only task artifact lists, content retrieval by artifact ID, UTF-8 text handling, final report rendering, and large-content avoidance.
- Link the guide to `schema/ts/router_contract.d.ts` and Router v1 JSON Schema files as the frontend type and contract references.
- Do not add or change public HTTP endpoints, Router v1 schema fields, generated JSON Schema, TypeScript declarations, database tables, or runtime behavior.

## Capabilities

### New Capabilities
- `frontend-api-usage-guide`: Documents how frontend clients consume the Router backend public API, SSE event stream, task state, artifacts, final reports, trace summaries, and Router v1 type references.

### Modified Capabilities
- None.

## Impact

- Affected documentation:
  - New or updated frontend API usage documentation under `docs/`.
  - Cross-references to existing `docs/backend.md`, `docs/architecture.md`, `docs/local-dev.md`, `schema/ts/router_contract.d.ts`, and `schema/*.schema.json`.
- Affected code:
  - None expected.
- Affected APIs:
  - No API behavior changes. The guide documents existing public endpoints:
    - `GET /health`
    - `GET /api/health`
    - `POST /api/tasks`
    - `GET /api/tasks/{task_id}`
    - `POST /api/tasks/{task_id}/messages`
    - `POST /api/tasks/{task_id}/cancel`
    - `GET /api/tasks/{task_id}/events`
    - `GET /api/tasks/{task_id}/artifacts`
    - `GET /api/artifacts/{artifact_id}`
    - `GET /api/tasks/{task_id}/trace`
- Affected users:
  - Frontend developers gain a single integration guide for building Chat Panel, Execution Timeline, Agent Cards, Artifact Panel, final report display, and debug/trace views.
