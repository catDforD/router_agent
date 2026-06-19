## Context

The Router backend already exposes frontend-facing FastAPI endpoints for task lifecycle, SSE event streaming, artifact access, and compact trace summaries. The relevant information is currently split across `docs/backend.md`, `docs/architecture.md`, `docs/local-dev.md`, `backend/app/api/*.py`, backend tests, `schema/ts/router_contract.d.ts`, and exported JSON Schema files.

The frontend needs to build Chat Panel, Execution Timeline, Agent Cards, Artifact Panel, final report display, and trace/debug views. Those views depend on integration details that are easy to miss if a frontend developer only reads the roadmap or generated OpenAPI output. In particular, `GET /api/tasks/{task_id}/events` is an SSE stream with `text/event-stream`, sequence-based resume semantics, and heartbeat frames; generated OpenAPI metadata is not enough to explain this behavior.

This change is documentation-only. It should capture how the current API is consumed without adding endpoint behavior, changing Router v1 schemas, or changing runtime execution.

## Goals / Non-Goals

**Goals:**

- Provide one frontend-facing API usage guide under `docs/`.
- Organize the guide by frontend workflow rather than backend module.
- Document all currently supported public frontend endpoints, including task lifecycle, health, events, artifacts, and trace summary.
- Make the SSE contract explicit enough for browser `EventSource` or equivalent clients.
- Explain artifact rendering rules, including metadata lists, content fetches, final report handling, and large-content boundaries.
- Point frontend developers to `schema/ts/router_contract.d.ts` and `schema/*.schema.json` for Router v1 data types.
- Preserve the current backend API and schema behavior.

**Non-Goals:**

- Do not implement frontend UI components.
- Do not add generated API clients.
- Do not add, rename, or remove backend HTTP endpoints.
- Do not change Router v1 Pydantic models, JSON Schema files, or TypeScript declarations.
- Do not change SSE runtime behavior, event visibility filtering, artifact visibility behavior, or trace summary projection.
- Do not document internal worker MCP APIs as frontend-callable APIs.

## Decisions

### Organize the guide around frontend workflows

The guide should start with the end-to-end frontend flow:

```text
create task
  -> subscribe to events
  -> render progress
  -> fetch task state when needed
  -> fetch artifact metadata
  -> fetch artifact content on demand
  -> render final report
  -> append clarification answers or cancel
  -> inspect trace summary for debug views
```

Alternative considered: list endpoints alphabetically. Rejected because frontend developers need sequencing, retry behavior, and UI mapping more than a raw route inventory.

### Treat Router v1 contracts as references, not duplicated source of truth

The guide should link to `schema/ts/router_contract.d.ts` and exported JSON Schema files for full field definitions. It should only include representative request and response snippets that are useful for frontend integration.

Alternative considered: duplicate full `TaskState`, `RouterEvent`, and `Artifact` definitions in the guide. Rejected because duplicated schema text will drift from Pydantic and JSON Schema contracts.

### Document SSE manually

The SSE section should explicitly describe:

- `Content-Type: text/event-stream`
- frame fields: `id`, `event`, and `data`
- `data` as a serialized Router v1 `RouterEvent`
- `id` as the per-task event `seq`
- `Last-Event-ID` resume support
- `after_seq` query parameter and its precedence over `Last-Event-ID`
- heartbeat frames such as `: keepalive`
- default exclusion of `visibility=internal` events

Alternative considered: rely on `/docs` OpenAPI UI. Rejected because OpenAPI does not adequately explain long-lived stream consumption or reconnect semantics for this endpoint.

### Document artifacts as lazy-loaded content

The guide should make `GET /api/tasks/{task_id}/artifacts` the metadata source for artifact panels and `GET /api/artifacts/{artifact_id}` the content source for selected artifacts. It should tell frontend developers not to expect large PLC code, reports, logs, patches, or replay logs inside `TaskState` or artifact list responses.

Alternative considered: recommend polling task state for content. Rejected because the backend architecture intentionally stores large content as artifacts and returns references in task state and events.

### Include trace summary as a debug/timeline projection

The guide should document `GET /api/tasks/{task_id}/trace` as a compact projection for execution timeline and developer/debug views. It should explain that the endpoint includes event summaries, artifact summaries, worker job summaries, gate result summaries, and main-agent run links, but omits large artifact content.

Alternative considered: omit trace summary because it is not in the original architecture route list. Rejected because the endpoint exists and is directly useful for frontend timeline/debug views.

## Risks / Trade-offs

- [Risk] Documentation can drift as APIs evolve. -> Mitigation: include a verification task that compares the guide against `backend/app/api/*.py` and the FastAPI OpenAPI path list.
- [Risk] The guide may accidentally imply frontend access to internal worker or MCP APIs. -> Mitigation: explicitly state that frontend clients only call the documented Router backend endpoints.
- [Risk] Full schema duplication would become stale. -> Mitigation: keep examples representative and link to TypeScript and JSON Schema contracts for complete field definitions.
- [Risk] Internal artifact or event visibility semantics may be misread as security guarantees. -> Mitigation: document current frontend filtering behavior and tell UI code to respect `visibility` fields when rendering.
- [Risk] Future endpoints such as pagination or downloads may be needed. -> Mitigation: keep this change documentation-only and open separate implementation changes for any missing API behavior discovered during frontend integration.

## Migration Plan

No runtime migration is required.

Implementation steps:

1. Add the frontend API usage guide under `docs/`.
2. Include workflow, endpoint reference, SSE, artifact, final report, trace, error handling, and type-reference sections.
3. Add cross-links from existing developer docs if useful.
4. Validate the guide against the currently registered FastAPI paths and relevant tests.

Rollback removes the new documentation and any cross-links. No code, database, schema, or API rollback is required.

## Open Questions

- Should the guide live at `docs/frontend-api.md` or under a nested path such as `docs/api/frontend.md`?
- Should future implementation changes expose a plain JSON event history endpoint in addition to SSE, or is SSE replay sufficient for the MVP frontend?
- Should artifact content downloads support non-UTF-8 or binary artifacts through a separate endpoint later?
