# event-streaming-api Specification

## Purpose
Provides a frontend-facing service and Server-Sent Events API for replaying and streaming persisted Router task events while preserving event visibility boundaries.
## Requirements
### Requirement: Event service appends through the persisted event log
The backend SHALL provide an event service that appends `RouterEvent` records through the existing event repository and returns the persisted event with the assigned per-task sequence number.

#### Scenario: Service append assigns sequence
- **WHEN** the event service appends a valid event for a task whose current `event_seq` is `0`
- **THEN** the returned event has `seq` equal to `1`

#### Scenario: Service append preserves append-only ordering
- **WHEN** the event service appends two valid events for the same task
- **THEN** the events are persisted with increasing `seq` values and are returned in sequence order

### Requirement: Event service exposes frontend-visible event history
The backend SHALL provide event service reads that return task events ordered by `seq` and hide `visibility=internal` events by default.

#### Scenario: User-visible event is returned
- **WHEN** a task has a persisted event with `visibility=user`
- **THEN** the default frontend-visible event read includes that event

#### Scenario: Internal event is hidden by default
- **WHEN** a task has a persisted event with `visibility=internal`
- **THEN** the default frontend-visible event read does not include that event

#### Scenario: Event history can be read after a cursor
- **WHEN** a task has visible events with `seq` values `1`, `2`, and `3`
- **THEN** reading visible events after `seq` `1` returns only events with `seq` values `2` and `3`

### Requirement: Event API streams task events with SSE
The backend SHALL expose `GET /api/tasks/{task_id}/events` as a Server-Sent Events stream for frontend-visible task events.

#### Scenario: SSE endpoint uses event stream content type
- **WHEN** a client opens `GET /api/tasks/{task_id}/events` for an existing task
- **THEN** the response uses a `text/event-stream` content type

#### Scenario: SSE endpoint replays existing visible events
- **WHEN** a task has existing `visibility=user` events before a client connects
- **THEN** the SSE stream emits those events ordered by `seq`

#### Scenario: SSE endpoint tails newly appended visible events
- **WHEN** a client is connected to the task event stream and a new `visibility=user` event is appended for that task
- **THEN** the SSE stream emits the new event without requiring a client polling request

#### Scenario: SSE endpoint excludes internal events
- **WHEN** a task has `visibility=internal` events
- **THEN** the default SSE stream does not emit those events

### Requirement: SSE event frames support deterministic resume
The backend SHALL format each Router event SSE frame with the Router event sequence number as the SSE `id`, the Router event type as the SSE `event`, and the serialized Router event as the SSE `data`.

#### Scenario: SSE frame contains event metadata
- **WHEN** a visible Router event with `seq` `2` and type `worker.started` is emitted
- **THEN** the SSE frame contains `id: 2`, `event: worker.started`, and `data` containing the serialized Router event payload

#### Scenario: Last-Event-ID resumes after emitted sequence
- **WHEN** a client reconnects with `Last-Event-ID` equal to `2`
- **THEN** the stream resumes with visible events whose `seq` is greater than `2`

#### Scenario: after_seq query parameter resumes after explicit sequence
- **WHEN** a client opens the event stream with `after_seq=2`
- **THEN** the stream starts with visible events whose `seq` is greater than `2`

### Requirement: Event API reports missing tasks
The backend SHALL report a missing task before opening an event stream.

#### Scenario: Missing task returns not found
- **WHEN** a client opens `GET /api/tasks/{task_id}/events` for a task ID that does not exist
- **THEN** the response status is `404`

### Requirement: Event stream remains alive while idle
The backend SHALL keep an open SSE stream observable while no new events are available.

#### Scenario: Idle stream emits heartbeat
- **WHEN** a client is connected to an event stream and no visible events are available during the heartbeat interval
- **THEN** the stream emits a valid SSE comment heartbeat frame

### Requirement: Development emitter supports manual SSE verification
The repository SHALL provide a local development script that appends representative user-visible lifecycle events for an existing task.

#### Scenario: Developer emits lifecycle events
- **WHEN** a developer runs the event emitter script with a valid task ID
- **THEN** the script appends representative `worker.started`, `artifact.created`, and `worker.completed` events that can be observed through `curl -N`

### Requirement: Event stream exposes Main Agent orchestration progress
The backend SHALL stream frontend-visible Main Agent orchestration progress events through the existing task SSE endpoint.

#### Scenario: Tool call event is streamed
- **WHEN** a connected client is streaming events for a task and the Main Agent selects a tool
- **THEN** the SSE stream emits a `main_agent.tool_called` event with the assigned Router event sequence number

#### Scenario: Tool result event is streamed
- **WHEN** a connected client is streaming events for a task and a Main Agent tool result is returned to the model
- **THEN** the SSE stream emits a `main_agent.tool_result` event with the assigned Router event sequence number

#### Scenario: Completed event is streamed before terminal success
- **WHEN** a successful Main Agent episode writes its final report and replay log
- **THEN** the SSE stream emits `main_agent.completed` before the terminal `task.succeeded` event for that episode

### Requirement: Event stream replay includes Main Agent observability events
The backend SHALL include persisted user-visible Main Agent observability events when replaying task event history.

#### Scenario: Reconnected client replays missed turn events
- **WHEN** a client reconnects to `GET /api/tasks/{task_id}/events` with `Last-Event-ID` before persisted Main Agent turn events
- **THEN** the stream replays `main_agent.turn_started`, `main_agent.tool_called`, `main_agent.tool_result`, and `main_agent.completed` events in sequence order

#### Scenario: Internal replay details remain hidden
- **WHEN** a task has internal-only Main Agent log or raw SDK diagnostic events
- **THEN** the default frontend-visible event stream excludes those internal events

### Requirement: Main Agent observability events are compact
The backend SHALL keep Main Agent observability event payloads bounded and suitable for frontend timeline rendering.

#### Scenario: Event payload references artifacts
- **WHEN** a Main Agent observability event relates to generated code, reports, logs, patches, or counterexamples
- **THEN** the event payload contains artifact IDs and summaries instead of full artifact content

#### Scenario: Rationale summary is bounded
- **WHEN** a Main Agent observability event includes a public rationale summary
- **THEN** the persisted summary is bounded to a configured maximum length or safely truncated before being emitted
