## Context

The backend already has Router v1 event models, JSON Schema exports, database rows, and `EventRepository.append_event` with per-task sequence allocation. The missing boundary is the runtime-facing service and frontend-facing streaming API described in `docs/backend.md` section 7.

Current constraints:

- `backend/app/services/event_service.py` and `backend/app/api/events.py` are empty.
- `EventRepository` persists events append-only and lists all events for a task, but it does not yet support frontend-safe filtering or incremental reads.
- The existing database stack uses synchronous SQLAlchemy sessions.
- Frontend consumers need `GET /api/tasks/{task_id}/events` with `Content-Type: text/event-stream`.
- `visibility=internal` events must not be exposed to frontend consumers by default.

## Goals / Non-Goals

**Goals:**

- Provide an `EventService` that centralizes event append, ordered event reads, visibility filtering, and stream iteration behavior.
- Provide a FastAPI SSE endpoint that replays existing visible events and then tails newly appended visible events.
- Make client reconnects deterministic by using per-task `seq` values as SSE event IDs and resume cursors.
- Keep event delivery grounded in the persisted append-only event log so late subscribers can replay history.
- Add tests for service behavior, API behavior, and SSE frame shape.
- Add a local development emitter script for `curl -N` verification.

**Non-Goals:**

- No Router v1 schema changes.
- No database migration changes.
- No WebSocket endpoint.
- No message broker, Redis, Postgres LISTEN/NOTIFY, or async database migration in this change.
- No full runtime orchestration, Main Agent integration, worker execution, or automatic artifact lifecycle event generation beyond the local development emitter.
- No production authorization or tenant-scoped filtering beyond preserving the existing `visibility` contract.

## Decisions

### Use the persisted event log as the source of truth

`EventService` should read and write through `EventRepository`. The service may build convenience methods around event construction and filtering, but the database remains the durable ordering authority.

Alternative considered: keep an in-memory event broadcaster and only write to the database as a side effect. That would make reconnect and replay behavior weaker, and it would lose events across process restarts.

### Extend repository reads for incremental, filtered access

Add a repository query that can return events for a task ordered by `seq`, optionally constrained by `after_seq`, `visibility`, and `limit`. Filtering should happen in SQL using projected columns instead of loading every event JSON payload and filtering in Python.

Alternative considered: keep only `list_events(task_id)` and filter in the service. That is acceptable for very small fixtures but scales poorly for long-running tasks and repeated SSE polling.

### Implement SSE with `StreamingResponse` and short database polling

The first implementation should use Starlette/FastAPI `StreamingResponse` and a small polling loop over the persisted event table. Each poll should open a short-lived session, read events after the last emitted `seq`, yield frames, and close the session before sleeping.

Alternative considered: hold a request-scoped synchronous SQLAlchemy session for the lifetime of the stream. That risks stale reads and ties up a database session for every connected browser.

Alternative considered: introduce Postgres LISTEN/NOTIFY or a broker-backed fanout channel now. That is a better later optimization, but it adds operational surface before the basic API contract exists.

### Use `seq` as the SSE event ID

Each emitted event frame should use:

```text
id: <event.seq>
event: <event.type>
data: <RouterEvent JSON>
```

Clients can resume using `Last-Event-ID` or the explicit `after_seq` query parameter. `after_seq` should win when both are provided because it is an explicit URL-level cursor.

Alternative considered: use `event_id` as the SSE ID. It is globally useful but does not provide a natural per-task ordering cursor.

### Preserve visibility at the event boundary

The default service and API path should emit only `visibility=user` events. Events that the frontend timeline must display, such as worker lifecycle summaries, should be written as user-visible events or paired with user-visible summary events. The SSE endpoint should not rewrite internal events into public events on the fly.

This resolves the tension between the existing `event.worker_started.valid.json` fixture, which is internal, and the backend plan, which expects frontend-visible worker lifecycle progress.

### Keep stream liveness explicit

When no events are available, the stream should periodically yield SSE comment heartbeat frames. This makes idle connections observable and reduces accidental proxy or client timeouts.

## Risks / Trade-offs

- [Risk] Polling adds database load for every connected stream. -> Mitigation: use an interval suitable for local MVP behavior, read only `seq > cursor`, and cap each batch.
- [Risk] Synchronous FastAPI streaming can occupy worker capacity while clients are connected. -> Mitigation: keep DB sessions short-lived and document this as an MVP transport that can later move to LISTEN/NOTIFY or a broker.
- [Risk] Internal events may be accidentally exposed if filtering is inconsistent. -> Mitigation: put default filtering in `EventService`, cover it with unit tests, and keep the API on the default filtered path.
- [Risk] Existing fixtures mark `worker.started` as internal while product docs expect worker lifecycle updates in the UI. -> Mitigation: tests for API visibility should use user-visible lifecycle events, and runtime producers should create public summary events when frontend display is required.
- [Risk] Clients may receive duplicate events after reconnect. -> Mitigation: use monotonic `seq` IDs and define resume as strictly greater than the cursor.

## Migration Plan

No data migration is required. The change can be deployed by adding the service, endpoint, route registration, tests, and development emitter script.

Rollback is straightforward: stop including the events router in `create_app`. Persisted events remain compatible with the existing repository and schema contracts.

## Open Questions

- Should an `include_internal` API switch exist for local-only debugging, or should internal visibility remain service-only?
- What polling interval and heartbeat interval should be the default for local development?
- Should the development emitter create `task.created` first, or only the worker lifecycle events listed in section 7 of `docs/backend.md`?
