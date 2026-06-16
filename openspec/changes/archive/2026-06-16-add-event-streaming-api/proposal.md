## Why

Router runtime events are already persisted with per-task sequence numbers, but the backend still lacks the service and HTTP streaming boundary that lets the frontend observe task progress without polling. This change turns the existing event log into a replayable Server-Sent Events API while preserving the append-only contract and default internal-event filtering.

## What Changes

- Add an event service layer that appends `RouterEvent` records through the existing repository and exposes ordered, frontend-safe event queries.
- Add `GET /api/tasks/{task_id}/events` as a `text/event-stream` endpoint that replays existing visible events and tails new visible events.
- Support reconnect-friendly event delivery using monotonic event sequence numbers, `Last-Event-ID`, and/or an `after_seq` query parameter.
- Keep `visibility=internal` events hidden from the frontend by default.
- Add focused unit and API coverage for event filtering, sequence behavior, replay, SSE formatting, and missing-task handling.
- Add a local development event emitter script so `curl -N` can verify live streaming behavior.

## Capabilities

### New Capabilities
- `event-streaming-api`: Provides a frontend-facing event service and SSE endpoint over persisted Router events.

### Modified Capabilities
- None.

## Impact

- Affected backend modules:
  - `backend/app/services/event_service.py`
  - `backend/app/api/events.py`
  - `backend/app/repositories/event_repo.py`
  - `backend/app/main.py`
- Affected tests:
  - New event service tests.
  - New event API/SSE tests.
  - Existing repository tests remain the source of truth for durable per-task sequence allocation.
- Affected developer tooling:
  - New `scripts/dev_emit_events.py` for manual SSE verification.
- No schema contract changes are expected for `RouterEvent`; this change consumes the existing Router v1 event model.
