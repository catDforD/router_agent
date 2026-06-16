## ADDED Requirements

### Requirement: Task API creates observable Router tasks
The backend SHALL expose `POST /api/tasks` to create a Router task from a user message and optional project context.

#### Scenario: Create task returns frontend task handle
- **WHEN** a client posts a JSON body with `message` and optional `project_context` to `POST /api/tasks`
- **THEN** the response status is `201` and the response body contains the new `task_id`, `status: "created"`, and `events_url: "/api/tasks/{task_id}/events"`

#### Scenario: Create task persists initial task state
- **WHEN** `POST /api/tasks` succeeds
- **THEN** the database contains a Router v1 `TaskState` with the returned task ID, `status` equal to `created`, `phase` equal to `intake`, `raw_user_request` equal to the request message, and `project_context` matching the request body

#### Scenario: Create task stores raw user request artifact
- **WHEN** `POST /api/tasks` succeeds
- **THEN** the artifact store contains a `raw_user_request` artifact for the task and the persisted `TaskState.current_artifacts.raw_user_request` references that artifact

#### Scenario: Create task emits visible task-created event
- **WHEN** `POST /api/tasks` succeeds
- **THEN** the task event log contains a user-visible `task.created` event whose correlation or payload identifies the raw user request artifact

#### Scenario: Create task rejects empty message
- **WHEN** a client posts an empty or blank `message` to `POST /api/tasks`
- **THEN** the API rejects the request with a client validation error and does not create a task

### Requirement: Task API returns current task state
The backend SHALL expose `GET /api/tasks/{task_id}` to return the current persisted Router v1 `TaskState`.

#### Scenario: Existing task is returned
- **WHEN** a client requests `GET /api/tasks/{task_id}` for an existing task
- **THEN** the response status is `200` and the response body contains the current `TaskState` serialized for JSON clients

#### Scenario: Missing task read returns not found
- **WHEN** a client requests `GET /api/tasks/{task_id}` for a task ID that does not exist
- **THEN** the response status is `404`

### Requirement: Task API appends user messages
The backend SHALL expose `POST /api/tasks/{task_id}/messages` to record follow-up user input for an existing non-terminal task.

#### Scenario: User message append stores message artifact
- **WHEN** a client posts a JSON body with `message` to `POST /api/tasks/{task_id}/messages` for an existing non-terminal task
- **THEN** the artifact store contains a new artifact linked to the task that records the user message without embedding it in `TaskState`

#### Scenario: User message append updates task observability
- **WHEN** `POST /api/tasks/{task_id}/messages` succeeds
- **THEN** the task state `updated_at` value is advanced and the task event log contains a user-visible `task.updated` event referencing the user message artifact

#### Scenario: Missing task message append returns not found
- **WHEN** a client posts to `POST /api/tasks/{task_id}/messages` for a task ID that does not exist
- **THEN** the response status is `404`

#### Scenario: Terminal task message append returns conflict
- **WHEN** a client posts a user message to a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** the API rejects the request with a conflict response and does not create a user message artifact

#### Scenario: User message append rejects empty message
- **WHEN** a client posts an empty or blank `message` to `POST /api/tasks/{task_id}/messages`
- **THEN** the API rejects the request with a client validation error and does not create a user message artifact

### Requirement: Task API cancels cancellable tasks
The backend SHALL expose `POST /api/tasks/{task_id}/cancel` to mark cancellable tasks as cancelled.

#### Scenario: Running task is cancelled
- **WHEN** a client posts to `POST /api/tasks/{task_id}/cancel` for a task whose status is `created`, `running`, or `waiting_user`
- **THEN** the persisted `TaskState` has `status` equal to `cancelled`, `phase` equal to `completed`, `completed_at` set, and `updated_at` advanced

#### Scenario: Cancellation emits visible event
- **WHEN** `POST /api/tasks/{task_id}/cancel` changes a task to `cancelled`
- **THEN** the task event log contains a user-visible `task.cancelled` event for that task

#### Scenario: Already cancelled task cancellation is idempotent
- **WHEN** a client posts to `POST /api/tasks/{task_id}/cancel` for a task whose status is already `cancelled`
- **THEN** the API returns the current cancelled task state without appending another `task.cancelled` event

#### Scenario: Completed task cancellation returns conflict
- **WHEN** a client posts to `POST /api/tasks/{task_id}/cancel` for a task whose status is `succeeded`, `partial_failed`, or `failed`
- **THEN** the API rejects the request with a conflict response and does not change the task state

#### Scenario: Missing task cancellation returns not found
- **WHEN** a client posts to `POST /api/tasks/{task_id}/cancel` for a task ID that does not exist
- **THEN** the response status is `404`

### Requirement: Task API integrates with existing app wiring
The backend SHALL register the task API router in the FastAPI application without changing existing health, artifact, or event endpoints.

#### Scenario: Task routes are available from created app
- **WHEN** the application is created through `create_app`
- **THEN** the task API routes are available alongside the existing health, artifact, and event routes

#### Scenario: Existing event stream observes task-created event
- **WHEN** a task is created through `POST /api/tasks` and a client opens `GET /api/tasks/{task_id}/events`
- **THEN** the existing event stream can emit the created task's user-visible `task.created` event
