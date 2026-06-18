# runtime-loop-background-execution Specification

## Purpose
TBD - created by archiving change add-runtime-loop-background-execution. Update Purpose after archive.
## Requirements
### Requirement: Task creation schedules background Runtime execution
The backend SHALL schedule Runtime execution after a task is successfully created without blocking the `POST /api/tasks` response on Main Agent or worker completion.

#### Scenario: Create task returns before background episode completes
- **WHEN** a client posts a valid task request to `POST /api/tasks`
- **THEN** the API response status is `201` and includes the task handle using the existing response shape
- **AND** the response is returned before the Main Agent episode is required to complete

#### Scenario: Background execution is scheduled after commit
- **WHEN** `POST /api/tasks` successfully persists the task, raw user request artifact, and `task.created` event
- **THEN** the backend schedules `RuntimeService.start_task` for the returned task ID after the request transaction commits
- **AND** the background runtime job uses its own database session

#### Scenario: Create failure does not schedule runtime
- **WHEN** task creation fails before the request transaction commits
- **THEN** the backend SHALL NOT schedule background Runtime execution for that failed request

### Requirement: Runtime executes Main Agent episodes through the service boundary
The backend SHALL provide a Runtime service that starts and runs one Main Agent episode for a persisted non-terminal task using configured runtime settings.

#### Scenario: Runtime starts a created task
- **WHEN** `RuntimeService.start_task` is invoked for an existing task with `status` equal to `created`
- **THEN** Runtime claims execution for the task
- **AND** invokes `MainAgentService.run_episode` with the configured artifact root, MCP mode, mock scenario, model, and max turns

#### Scenario: Mock happy path reaches terminal success
- **WHEN** Runtime starts an ordinary mock development task and the Main Agent runner completes dev, test, Quality Gate, and finish actions
- **THEN** the persisted task eventually has `status` equal to `succeeded`
- **AND** the event log includes Main Agent, worker, artifact, gate, and terminal task events

#### Scenario: Terminal task is not re-run
- **WHEN** `RuntimeService.start_task` or `RuntimeService.run_main_agent_episode` is invoked for a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** Runtime does not invoke a new Main Agent episode
- **AND** no new worker job is created by that Runtime invocation

### Requirement: Runtime prevents overlapping episodes for one task
The backend SHALL prevent concurrent Runtime invocations from running overlapping Main Agent episodes for the same task.

#### Scenario: Running lease blocks duplicate start
- **WHEN** one Runtime invocation has claimed a non-expired runtime episode lease for a task
- **AND** another Runtime invocation attempts to start or resume the same task
- **THEN** the second invocation exits without invoking Main Agent
- **AND** the task has no duplicate `main_agent.started` event for the skipped invocation

#### Scenario: Lease is released after pause or terminal completion
- **WHEN** a Runtime episode reaches `waiting_user`, `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** Runtime releases or marks idle the runtime episode lease in task metadata

#### Scenario: Expired lease can be reclaimed
- **WHEN** a task has a runtime episode lease whose `lease_until` is in the past
- **THEN** a new Runtime invocation may reclaim the task and run a new episode if the task is otherwise runnable

### Requirement: Runtime checkpoints progress for task reads and event streams
The backend SHALL commit meaningful Runtime progress checkpoints so other database sessions can observe task state and user-visible events while background execution is running.

#### Scenario: Main Agent start is visible before episode completion
- **WHEN** background Runtime starts a Main Agent episode
- **THEN** `GET /api/tasks/{task_id}` can observe the task's latest Main Agent trace state after the start checkpoint
- **AND** `GET /api/tasks/{task_id}/events` can emit a user-visible `main_agent.started` event before the full episode is required to complete

#### Scenario: Worker progress is visible during background execution
- **WHEN** background Runtime dispatches a worker through Main Agent tools
- **THEN** worker start, artifact creation, and worker terminal events are committed at checkpoints
- **AND** an SSE client connected to the task event stream can receive those events without waiting for final task completion

#### Scenario: Terminal completion is visible
- **WHEN** background Runtime completes a task through Quality Gate and `finish_task`
- **THEN** the terminal task state and terminal task event are committed and visible through the task read API and event stream

### Requirement: User messages resume waiting tasks
The backend SHALL resume Runtime execution in the background after a user follow-up message is accepted for a non-terminal task.

#### Scenario: User message schedules resume
- **WHEN** a client posts a valid message to `POST /api/tasks/{task_id}/messages`
- **THEN** the backend persists the user message artifact and `task.updated` event using existing Task API behavior
- **AND** schedules `RuntimeService.resume_after_user_message` for that task after the request transaction commits

#### Scenario: Waiting task resumes after clarification answer
- **WHEN** Runtime resumes a task whose status is `waiting_user` and whose required clarification questions are open
- **THEN** Runtime records the latest user message as the answer context for those open required questions
- **AND** marks those questions as answered
- **AND** moves the task to `status` equal to `running` and `phase` equal to `planning` before invoking a new Main Agent episode

#### Scenario: Resume no-ops for terminal task
- **WHEN** `RuntimeService.resume_after_user_message` starts and the task is terminal
- **THEN** Runtime does not invoke Main Agent
- **AND** no worker job is created by that resume invocation

### Requirement: Runtime preserves cancellation safety
The backend SHALL treat cancelled tasks as terminal for Runtime scheduling, worker dispatch, and finalization.

#### Scenario: Cancelled task is not started
- **WHEN** `POST /api/tasks/{task_id}/cancel` has marked a task `cancelled`
- **AND** a scheduled Runtime start or resume later runs for that task
- **THEN** Runtime does not invoke Main Agent
- **AND** Runtime does not create a worker job

#### Scenario: Cancellation prevents later worker dispatch
- **WHEN** a task is cancelled before the Main Agent requests another worker action
- **THEN** the runtime tool path rejects new worker dispatch for that terminal task
- **AND** no new worker job, worker event, or worker artifact is created after cancellation

#### Scenario: Cancellation is not overwritten by finish
- **WHEN** a task has already been marked `cancelled`
- **AND** a stale Main Agent episode or tool call attempts to finish the task as `succeeded`
- **THEN** the finish action is rejected
- **AND** the persisted task status remains `cancelled`

### Requirement: Runtime failures are observable and do not create false success
The backend SHALL record Runtime or Main Agent execution failures without falsely marking a task successful.

#### Scenario: Runtime exception records failure metadata
- **WHEN** a background Runtime invocation raises an unhandled exception while processing an existing task
- **THEN** Runtime records observable error information in task metadata or a user-visible task event
- **AND** releases or expires the runtime episode lease
- **AND** does not mark the task `succeeded` because of that exception

#### Scenario: Main Agent model behavior failure is checkpointed
- **WHEN** `MainAgentService.run_episode` returns an error output for max turns or model behavior failure
- **THEN** Runtime commits the emitted Main Agent error event or equivalent observable state
- **AND** does not create a false terminal success state

