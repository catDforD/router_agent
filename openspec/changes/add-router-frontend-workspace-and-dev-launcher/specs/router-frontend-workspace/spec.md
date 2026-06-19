## ADDED Requirements

### Requirement: Frontend workspace starts at the task experience
The frontend SHALL present a Router task workspace as the primary screen, allowing a user to create and inspect a Router task without navigating through a marketing or documentation landing page.

#### Scenario: Empty workspace is usable
- **WHEN** a user opens the frontend before creating a task
- **THEN** the workspace provides a task input area, project context controls for PLC language and platform, backend connection status, and no requirement for an existing task ID

#### Scenario: Created task opens live workspace
- **WHEN** task creation succeeds through `POST /api/tasks`
- **THEN** the workspace stores the returned `task_id`, opens the returned task event stream, fetches current task state, and renders task status and phase

### Requirement: Frontend consumes only public Router APIs
The frontend SHALL call only the documented Router backend public endpoints for task lifecycle, event streaming, artifacts, trace summary, and health checks.

#### Scenario: API helper modules map to public endpoints
- **WHEN** frontend API helpers are implemented
- **THEN** they expose functions for creating tasks, reading task state, appending user messages, cancelling tasks, opening task events, listing artifacts, reading artifact content, reading trace summary, and checking health
- **AND** they do not call internal worker, MCP, database, local artifact file, or Main Agent function-tool APIs

#### Scenario: Router v1 types remain contract-aligned
- **WHEN** frontend code types Router payloads
- **THEN** the types are imported, generated, or maintained from `schema/ts/router_contract.d.ts` rather than copied ad hoc inside component files

### Requirement: Workspace streams and resumes task events
The frontend SHALL consume `GET /api/tasks/{task_id}/events` as Server-Sent Events and maintain deterministic resume behavior.

#### Scenario: Events update the live timeline
- **WHEN** the event stream emits a `RouterEvent`
- **THEN** the workspace appends a de-duplicated event row ordered by `seq`
- **AND** the visible timeline identifies the event type, title, message, severity, source, timestamp, and relevant worker/artifact/failure correlations

#### Scenario: Event stream reconnects after a cursor
- **WHEN** the SSE connection is interrupted after the frontend has observed event sequence `N`
- **THEN** the frontend reconnects with `after_seq=N`
- **AND** it does not duplicate already rendered events after reconnecting

#### Scenario: Heartbeats do not alter task state
- **WHEN** the SSE stream emits heartbeat comment frames
- **THEN** the frontend ignores those frames for task state, timeline, artifact, and trace rendering

### Requirement: Workspace combines task state with event projections
The frontend SHALL render task state from `TaskState` while using events as the live progress log.

#### Scenario: Task state renders lifecycle and execution state
- **WHEN** `GET /api/tasks/{task_id}` returns `TaskState`
- **THEN** the workspace displays task `status`, `phase`, `task_type`, difficulty level, gate requirements and results, repair round budget, current artifact refs, active worker jobs, unresolved questions, failures, and trace identifiers where available

#### Scenario: Key events trigger state refresh
- **WHEN** the frontend receives task lifecycle, worker completion, gate, clarification, artifact, Main Agent completion, or terminal events
- **THEN** it refreshes the relevant compact projections such as `TaskState`, artifact metadata, final report content, or trace summary without relying on event payloads as complete replacement state

### Requirement: Workspace renders worker, gate, and repair progress
The frontend SHALL provide execution views that make Main Agent orchestration, worker execution, quality gates, and repair loops understandable without exposing hidden reasoning.

#### Scenario: Agent cards represent worker state
- **WHEN** worker job references or worker events exist for `plc-dev`, `plc-test`, `plc-formal`, or `plc-repair`
- **THEN** the workspace displays cards for those workers with status, objective or summary, start/completion time, produced artifact refs, and failure indicators when present

#### Scenario: Quality gate readiness is visible
- **WHEN** task gates or trace gate results are available
- **THEN** the workspace shows requirements, code, test, formal, regression, and final gate readiness using pass/fail/pending states and links to evidence artifact IDs when present

#### Scenario: Repair rounds are visible
- **WHEN** repair events or `TaskState.runtime_limits.repair_rounds` indicate repair activity
- **THEN** the workspace displays current repair round count, maximum repair rounds, pending regression flags, and blocking failure state

### Requirement: Workspace supports clarification and cancellation flows
The frontend SHALL allow users to respond to open clarification questions and cancel cancellable tasks through the public task API.

#### Scenario: Required clarification is answered
- **WHEN** `TaskState.status` is `waiting_user` and open required clarification questions exist
- **THEN** the workspace displays the questions and allows the user to submit a follow-up message through `POST /api/tasks/{task_id}/messages`
- **AND** the workspace uses the returned task state and subsequent SSE events to resume rendering progress

#### Scenario: Cancellable task can be cancelled
- **WHEN** a task is in `created`, `running`, or `waiting_user` status
- **THEN** the workspace exposes a cancel action that calls `POST /api/tasks/{task_id}/cancel`
- **AND** it updates the UI from the returned `TaskState` and terminal cancel event

#### Scenario: Terminal task disables mutation actions
- **WHEN** a task status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** the workspace disables message append and cancellation actions that would conflict with terminal task state

### Requirement: Workspace lists and lazy-loads artifacts
The frontend SHALL render artifact metadata from the task artifact list and fetch artifact content only when needed.

#### Scenario: Artifact list renders metadata
- **WHEN** `GET /api/tasks/{task_id}/artifacts` returns artifact metadata
- **THEN** the workspace displays artifact type, name/display name, version, status, visibility, summary, size, MIME type, creator, timestamp, and current/latest relationship when known

#### Scenario: Artifact content loads on selection
- **WHEN** a user selects an available artifact
- **THEN** the frontend calls `GET /api/artifacts/{artifact_id}` and renders UTF-8 content according to artifact type and MIME type

#### Scenario: Unsupported artifact preview is handled
- **WHEN** artifact content cannot be displayed inline because the API returns an unsupported content response such as non-UTF-8 content
- **THEN** the workspace displays artifact metadata and an explicit unsupported preview state without crashing the task workspace

### Requirement: Workspace renders final report as the completion summary
The frontend SHALL use the `final_report` artifact as the primary completed-task summary when it is available.

#### Scenario: Final report is discovered
- **WHEN** task state, artifact metadata, or `main_agent.completed` event payload references a `final_report` artifact
- **THEN** the workspace fetches that artifact content and makes it available in the final report view

#### Scenario: Final report displays delivery sections
- **WHEN** the final report content is a Router final report JSON payload
- **THEN** the workspace renders final status, user goal, classification, summary, plan, decisions, delivery artifact refs, validation summary, repair summary, assumptions, unresolved items, gate summary, and trace refs when present

### Requirement: Workspace provides trace debug views
The frontend SHALL expose a debug-oriented trace view backed by `GET /api/tasks/{task_id}/trace`.

#### Scenario: Trace view shows compact execution graph
- **WHEN** trace summary is loaded for a task
- **THEN** the trace view shows main-agent runs, worker jobs, artifact summaries, gate result summaries, terminal event information, and event summaries without loading large artifact content

#### Scenario: Trace view links related entities
- **WHEN** trace summary entities share worker job IDs, artifact IDs, failure IDs, or main agent run IDs
- **THEN** the frontend allows a user to navigate between related timeline rows, worker cards, artifacts, gates, and trace rows

### Requirement: Workspace handles API errors and connection states
The frontend SHALL provide visible, recoverable states for backend health, request failures, validation errors, mutation conflicts, missing resources, and SSE connection lifecycle.

#### Scenario: Backend unavailable is visible
- **WHEN** health checks or primary API calls fail because the backend is unavailable
- **THEN** the workspace displays a backend connection error and does not present stale task execution as live

#### Scenario: Validation and conflict errors are surfaced
- **WHEN** task creation, user message append, cancellation, artifact read, or trace read returns a documented error status
- **THEN** the workspace displays the error message in the relevant panel and allows retry where the operation is safe to retry

#### Scenario: SSE state is visible
- **WHEN** the event stream is connecting, connected, reconnecting, or closed after terminal task completion
- **THEN** the workspace displays the current stream state without blocking access to already loaded task state and artifacts
