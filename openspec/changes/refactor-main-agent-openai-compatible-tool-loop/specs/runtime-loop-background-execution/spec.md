## MODIFIED Requirements

### Requirement: Runtime executes Main Agent episodes through the service boundary
The backend SHALL provide a Runtime service that starts and runs one Main Agent episode for a persisted non-terminal task using configured runtime settings.

#### Scenario: Runtime starts a created task
- **WHEN** `RuntimeService.start_task` is invoked for an existing task with `status` equal to `created`
- **THEN** Runtime claims execution for the task
- **AND** invokes `MainAgentService.run_episode` with the configured artifact root, MCP mode, mock scenario, Main Agent provider settings, and max turns

#### Scenario: Mock happy path reaches terminal success
- **WHEN** Runtime starts an ordinary mock development task and the Main Agent tool-loop runner completes dev, test, Quality Gate, final report, and finish tool actions
- **THEN** the persisted task eventually has `status` equal to `succeeded`
- **AND** the event log includes Main Agent public message, tool, worker, artifact, gate, report, and terminal task events

#### Scenario: Terminal task is not re-run
- **WHEN** `RuntimeService.start_task` or `RuntimeService.run_main_agent_episode` is invoked for a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** Runtime does not invoke a new Main Agent episode
- **AND** no new worker job is created by that Runtime invocation

### Requirement: Runtime checkpoints progress for task reads and event streams
The backend SHALL commit meaningful Runtime progress checkpoints so other database sessions can observe task state and user-visible events while background execution is running.

#### Scenario: Main Agent start is visible before episode completion
- **WHEN** background Runtime starts a Main Agent episode
- **THEN** `GET /api/tasks/{task_id}` can observe the task's latest Main Agent trace state after the start checkpoint
- **AND** `GET /api/tasks/{task_id}/events` can emit a user-visible `main_agent.started` event before the full episode is required to complete

#### Scenario: Main Agent public progress is visible during background execution
- **WHEN** background Runtime receives public Main Agent messages, plan updates, tool calls, or tool results
- **THEN** checkpoints make those user-visible events available to task reads and SSE clients before final task completion

#### Scenario: Worker progress is visible during background execution
- **WHEN** background Runtime dispatches a worker through Main Agent tools
- **THEN** worker start, artifact creation, and worker terminal events are committed at checkpoints
- **AND** an SSE client connected to the task event stream can receive those events without waiting for final task completion

#### Scenario: Terminal completion is visible
- **WHEN** background Runtime completes a task through Quality Gate, final report, and `finish_task`
- **THEN** the terminal task state and terminal task event are committed and visible through the task read API and event stream
