## Why

The backend can already persist task state, store artifacts, and stream task events, but the frontend still lacks a first-class API for creating and managing tasks. This change closes that gap so a user request can enter the system through HTTP and become an observable Router task without relying on seed scripts.

## What Changes

- Add a frontend-facing Task API:
  - `POST /api/tasks`
  - `GET /api/tasks/{task_id}`
  - `POST /api/tasks/{task_id}/messages`
  - `POST /api/tasks/{task_id}/cancel`
- Add a task service boundary that creates an initial Router v1 `TaskState`, persists the raw user request as a `raw_user_request` artifact, and appends a user-visible `task.created` event.
- Add task read behavior that returns the current persisted `TaskState`.
- Add user message append behavior that records follow-up user input and emits a task update event without starting or resuming the Main Agent in this change.
- Add cancellation behavior that marks cancellable tasks as `cancelled` and emits a user-visible `task.cancelled` event.
- Keep Main Agent execution, background runtime scheduling, worker interruption, timeout handling, and detailed difficulty classification out of scope for this change.

## Capabilities

### New Capabilities

- `task-api`: Provides HTTP endpoints and service behavior for creating tasks, reading task state, appending user messages, and cancelling tasks.

### Modified Capabilities

- None.

## Impact

- Affected backend modules:
  - `backend/app/api/tasks.py`
  - `backend/app/services/task_service.py`
  - `backend/app/main.py`
  - `backend/app/core/ids.py`
  - `backend/app/core/time.py`
- Affected tests:
  - New task service tests for creation, artifact/event side effects, user messages, cancellation, and missing-task behavior.
  - New Task API tests using the existing FastAPI `TestClient` and temporary SQLite pattern.
- Affected local verification:
  - `POST /api/tasks` should create a database task, write a `raw_user_request` artifact, and make `task.created` visible through the existing event stream.
- No Router v1 schema enum values, JSON Schema files, TypeScript declarations, database migrations, worker contracts, or OpenAI Agent integrations are expected to change.
