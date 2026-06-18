## ADDED Requirements

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
