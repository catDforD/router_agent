## 1. Core Helpers And Request Models

- [x] 1.1 Add UTC timestamp helper(s) in `backend/app/core/time.py` for timezone-aware creation/update times.
- [x] 1.2 Add ID helper(s) in `backend/app/core/ids.py` for task, session, artifact, and event IDs with stable prefixes.
- [x] 1.3 Define Task API request/response models in `backend/app/api/tasks.py` or a local service boundary, including non-blank validation for `message`.
- [x] 1.4 Define service-level conflict error(s) for terminal task mutations that should map to HTTP `409`.

## 2. Task Service

- [x] 2.1 Implement `TaskService.get_task(task_id)` using `TaskRepository`.
- [x] 2.2 Implement initial `TaskState` construction with conservative defaults, valid runtime limits, empty worker/failure/question lists, and provided project context.
- [x] 2.3 Implement `TaskService.create_task` to persist the task, write a `raw_user_request` artifact, append a user-visible `task.created` event, and return the final persisted task state.
- [x] 2.4 Ensure task creation does not overwrite `current_artifacts` or `event_seq` after artifact and event side effects.
- [x] 2.5 Implement `TaskService.append_user_message` to reject terminal tasks, store the message as a task-linked artifact, advance task observability, and append a user-visible `task.updated` event.
- [x] 2.6 Implement `TaskService.cancel_task` to mark `created`, `running`, and `waiting_user` tasks as `cancelled`, set completion timestamps, and append `task.cancelled`.
- [x] 2.7 Make cancellation of an already `cancelled` task idempotent without appending a duplicate cancellation event.
- [x] 2.8 Reject cancellation for `succeeded`, `partial_failed`, and `failed` tasks with a service conflict.

## 3. Task API Wiring

- [x] 3.1 Implement database session dependency and service construction for `backend/app/api/tasks.py` using app settings.
- [x] 3.2 Implement `POST /api/tasks` with HTTP `201`, minimal response payload, and repository/store conflict translation.
- [x] 3.3 Implement `GET /api/tasks/{task_id}` returning the current `TaskState` JSON payload.
- [x] 3.4 Implement `POST /api/tasks/{task_id}/messages` returning the updated task/message result and translating missing or terminal tasks to `404`/`409`.
- [x] 3.5 Implement `POST /api/tasks/{task_id}/cancel` returning the cancelled/current task state and translating missing or terminal tasks to `404`/`409`.
- [x] 3.6 Register the task router in `backend/app/main.py` without changing existing health, artifact, or event routes.

## 4. Tests

- [x] 4.1 Add task service tests proving task creation persists `TaskState` with request message and project context.
- [x] 4.2 Add task service tests proving task creation writes a `raw_user_request` artifact and updates `TaskState.current_artifacts.raw_user_request`.
- [x] 4.3 Add task service tests proving task creation appends a user-visible `task.created` event with artifact correlation.
- [x] 4.4 Add task service tests for appending a user message artifact and `task.updated` event.
- [x] 4.5 Add task service tests for cancellation state transition, idempotent already-cancelled behavior, and terminal-task conflicts.
- [x] 4.6 Add Task API tests for `POST /api/tasks`, including response shape, persisted task, raw request artifact, and visible event stream compatibility.
- [x] 4.7 Add Task API tests for `GET /api/tasks/{task_id}` success and missing-task `404`.
- [x] 4.8 Add Task API tests for `POST /api/tasks/{task_id}/messages` success, missing task, terminal-task conflict, and blank message validation.
- [x] 4.9 Add Task API tests for `POST /api/tasks/{task_id}/cancel` success, idempotent cancelled task, terminal-task conflict, and missing task.

## 5. Validation

- [x] 5.1 Run focused task service and Task API tests.
- [x] 5.2 Run existing artifact and event API tests to confirm route wiring did not regress.
- [x] 5.3 Run `uv run python -m compileall backend`.
- [x] 5.4 Run `git diff --check`.
- [x] 5.5 Manually verify `POST /api/tasks` creates a task, stores a `raw_user_request` artifact, and exposes `task.created` through `GET /api/tasks/{task_id}/events`.
