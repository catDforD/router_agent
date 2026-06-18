## ADDED Requirements

### Requirement: Frontend API guide documents supported public endpoints
The repository SHALL provide a frontend API usage guide that identifies the Router backend endpoints intended for frontend clients and describes their expected use without introducing new API behavior.

#### Scenario: Guide lists task lifecycle endpoints
- **WHEN** a frontend developer reads the guide
- **THEN** the guide SHALL document `POST /api/tasks`, `GET /api/tasks/{task_id}`, `POST /api/tasks/{task_id}/messages`, and `POST /api/tasks/{task_id}/cancel`
- **AND** it SHALL include representative request and response examples for task creation and user message appends

#### Scenario: Guide lists read-only observability endpoints
- **WHEN** a frontend developer reads the guide
- **THEN** the guide SHALL document `GET /api/tasks/{task_id}/events`, `GET /api/tasks/{task_id}/artifacts`, `GET /api/artifacts/{artifact_id}`, and `GET /api/tasks/{task_id}/trace`
- **AND** it SHALL explain which frontend views commonly consume each endpoint

#### Scenario: Guide lists health endpoints
- **WHEN** a frontend developer reads the guide
- **THEN** the guide SHALL document `GET /health` and `GET /api/health` as health-check endpoints

### Requirement: Frontend API guide documents the task workflow
The guide SHALL describe the recommended frontend flow for creating a Router task and rendering its progress and results.

#### Scenario: Happy path workflow is documented
- **WHEN** a frontend developer follows the guide
- **THEN** the guide SHALL describe creating a task, subscribing to the returned `events_url`, rendering progress from Router events, fetching task state, listing artifacts, and reading selected artifact content

#### Scenario: Clarification workflow is documented
- **WHEN** a task enters `waiting_user` or emits a clarification event
- **THEN** the guide SHALL describe posting follow-up user input to `POST /api/tasks/{task_id}/messages`
- **AND** it SHALL explain that the response includes the updated `TaskState` and `message_artifact_id`

#### Scenario: Cancellation workflow is documented
- **WHEN** a frontend needs to cancel a cancellable task
- **THEN** the guide SHALL describe using `POST /api/tasks/{task_id}/cancel`
- **AND** it SHALL summarize idempotent cancellation and conflict behavior for terminal tasks

### Requirement: Frontend API guide documents SSE event consumption
The guide SHALL define how frontend clients consume task events from the SSE endpoint.

#### Scenario: SSE frame shape is documented
- **WHEN** a frontend developer reads the event stream section
- **THEN** the guide SHALL state that `GET /api/tasks/{task_id}/events` returns `Content-Type: text/event-stream`
- **AND** each Router event frame SHALL be documented as containing `id: <seq>`, `event: <event.type>`, and `data: <RouterEvent JSON>`

#### Scenario: SSE resume behavior is documented
- **WHEN** a frontend developer reads the event stream section
- **THEN** the guide SHALL document reconnecting with `Last-Event-ID`
- **AND** it SHALL document the `after_seq` query parameter
- **AND** it SHALL state that `after_seq` takes precedence when both `after_seq` and `Last-Event-ID` are provided

#### Scenario: SSE visibility and heartbeat behavior is documented
- **WHEN** a frontend developer reads the event stream section
- **THEN** the guide SHALL explain that default frontend-visible streams exclude `visibility=internal` events
- **AND** it SHALL describe heartbeat frames such as `: keepalive`

### Requirement: Frontend API guide documents task state usage
The guide SHALL identify the `TaskState` fields frontend clients should commonly use for high-level rendering without duplicating the full Router v1 contract.

#### Scenario: Status and phase rendering guidance is documented
- **WHEN** a frontend developer reads the task state section
- **THEN** the guide SHALL identify `status`, `phase`, `task_type`, `difficulty`, `gates`, `current_artifacts`, `unresolved_questions`, `failures`, `trace`, and `event_seq` as useful frontend fields
- **AND** it SHALL link to `schema/ts/router_contract.d.ts` for full `TaskState` typing

#### Scenario: Large content boundary is documented
- **WHEN** a frontend developer reads the task state section
- **THEN** the guide SHALL state that large PLC code, reports, logs, patches, and replay content are represented by artifact references rather than embedded directly in `TaskState`

### Requirement: Frontend API guide documents artifact retrieval and rendering
The guide SHALL explain how frontend clients list artifacts and fetch artifact content for display.

#### Scenario: Artifact metadata list behavior is documented
- **WHEN** a frontend developer reads the artifact section
- **THEN** the guide SHALL state that `GET /api/tasks/{task_id}/artifacts` returns task artifact metadata
- **AND** it SHALL state that the list response does not include artifact content

#### Scenario: Artifact content read behavior is documented
- **WHEN** a frontend developer reads the artifact section
- **THEN** the guide SHALL state that `GET /api/artifacts/{artifact_id}` returns artifact metadata plus UTF-8 text content when available
- **AND** it SHALL document the `content`, `content_encoding`, `mime_type`, `size_bytes`, and `content_hash` response fields

#### Scenario: Artifact panel mapping is documented
- **WHEN** a frontend developer reads the artifact section
- **THEN** the guide SHALL map common artifact types such as `plc_code`, `io_contract`, `test_report`, `formal_report`, `counterexample`, `patch`, `gate_report`, `final_report`, and `main_agent_log` to likely UI rendering approaches

### Requirement: Frontend API guide documents final report and trace summary usage
The guide SHALL explain how frontend clients discover final reports and use trace summaries for timeline or debug views.

#### Scenario: Final report discovery is documented
- **WHEN** a task completes or partially fails
- **THEN** the guide SHALL explain how to find the `final_report` artifact through `TaskState.current_artifacts.final_report` or the task artifact list
- **AND** it SHALL explain that the final report content is fetched through `GET /api/artifacts/{artifact_id}`

#### Scenario: Trace summary endpoint is documented
- **WHEN** a frontend developer reads the trace section
- **THEN** the guide SHALL document `GET /api/tasks/{task_id}/trace`
- **AND** it SHALL explain that the trace response is a compact projection of main-agent runs, worker jobs, artifacts, gate results, and events without embedding large artifact content

### Requirement: Frontend API guide documents error handling
The guide SHALL summarize frontend-relevant error statuses for documented endpoints.

#### Scenario: Validation and missing-resource errors are documented
- **WHEN** a frontend developer reads the error handling section
- **THEN** the guide SHALL document `422` validation errors for invalid request bodies or invalid query parameters
- **AND** it SHALL document `404` responses for missing tasks or artifacts

#### Scenario: Conflict and content errors are documented
- **WHEN** a frontend developer reads the error handling section
- **THEN** the guide SHALL document `409` responses for mutation conflicts or invalid artifact storage
- **AND** it SHALL document `415` responses for non-UTF-8 artifact content
- **AND** it SHALL document `500` responses for artifact content read failures

### Requirement: Frontend API guide links contract references
The guide SHALL identify the canonical frontend type and schema references for Router v1 payloads.

#### Scenario: TypeScript contract reference is documented
- **WHEN** a frontend developer reads the type reference section
- **THEN** the guide SHALL link to `schema/ts/router_contract.d.ts`
- **AND** it SHALL state that the TypeScript file is the human-readable frontend reference for Router v1 payloads

#### Scenario: JSON Schema reference is documented
- **WHEN** a frontend developer reads the type reference section
- **THEN** the guide SHALL link to exported JSON Schema files under `schema/`
- **AND** it SHALL identify them as the language-neutral contract references for task state, worker inputs/results, artifacts, and events
