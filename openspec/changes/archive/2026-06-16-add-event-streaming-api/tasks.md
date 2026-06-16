## 1. Repository And Service

- [x] 1.1 Extend `EventRepository` with an ordered incremental query that supports `after_seq`, `visibility`, and `limit` without changing existing append semantics.
- [x] 1.2 Implement `EventService.append_event` as the service entrypoint for persisted Router event writes.
- [x] 1.3 Implement `EventService.list_visible_events` or equivalent frontend-safe read method that hides `visibility=internal` by default.
- [x] 1.4 Add service helpers for cursor normalization from `after_seq` and `Last-Event-ID`.
- [x] 1.5 Add service or API utilities for serializing Router events into SSE frames using `seq` as `id`, `type` as `event`, and JSON Router event payload as `data`.

## 2. SSE API

- [x] 2.1 Implement `backend/app/api/events.py` with `GET /api/tasks/{task_id}/events`.
- [x] 2.2 Validate task existence before opening the stream and return `404` for missing tasks.
- [x] 2.3 Implement replay of existing user-visible events after the resolved cursor.
- [x] 2.4 Implement tailing of newly appended user-visible events with short-lived database sessions per poll.
- [x] 2.5 Emit SSE heartbeat comment frames while the stream is idle.
- [x] 2.6 Register the events router in `backend/app/main.py`.

## 3. Development Tooling

- [x] 3.1 Add `scripts/dev_emit_events.py` that accepts `--task-id` and appends representative user-visible lifecycle events.
- [x] 3.2 Ensure the development emitter creates `worker.started`, `artifact.created`, and `worker.completed` events with valid Router v1 payloads.
- [x] 3.3 Document or print the `curl -N http://localhost:8000/api/tasks/{task_id}/events` verification command from the emitter workflow.

## 4. Tests And Validation

- [x] 4.1 Add event service tests for append sequence assignment and ordered reads.
- [x] 4.2 Add event service tests proving user-visible events are returned and internal events are hidden by default.
- [x] 4.3 Add event service tests for `after_seq` cursor reads.
- [x] 4.4 Add API tests for `text/event-stream` content type and existing-event replay.
- [x] 4.5 Add API tests for SSE frame shape, including `id`, `event`, and serialized `data`.
- [x] 4.6 Add API tests for `Last-Event-ID` and explicit `after_seq` resume behavior.
- [x] 4.7 Add API tests for missing task `404` behavior.
- [x] 4.8 Run `uv run pytest backend/app/tests/unit/test_event_service.py backend/app/tests/unit/test_event_api.py -q`.
- [x] 4.9 Run `uv run python -m compileall backend` and `git diff --check`.
