## Context

The repository already has the lower-level runtime pieces needed for a frontend task entrypoint:

- `TaskRepository` persists and restores complete Router v1 `TaskState` payloads.
- `ArtifactStore` writes local artifact content, creates artifact metadata, and updates `TaskState.current_artifacts`.
- `EventService` appends persisted Router events and the existing SSE API streams user-visible events.
- `backend/app/api/tasks.py`, `backend/app/services/task_service.py`, `backend/app/core/ids.py`, and `backend/app/core/time.py` are currently empty.

The Task API should use those existing boundaries instead of introducing a new persistence path. Its first responsibility is to turn a user HTTP request into a durable, observable task shell that future runtime changes can execute.

## Goals / Non-Goals

**Goals:**

- Provide frontend-facing HTTP endpoints for task creation, task state reads, user message append, and task cancellation.
- Create a valid initial Router v1 `TaskState` using conservative defaults.
- Store the original user request as a `raw_user_request` artifact and attach its `ArtifactRef` to the task state.
- Emit user-visible lifecycle events for `task.created`, `task.updated`, and `task.cancelled`.
- Keep API request handling transactional: database state, artifact metadata, and emitted events must commit together or roll back together.
- Follow the existing FastAPI dependency and test patterns used by artifact and event APIs.

**Non-Goals:**

- Do not start Main Agent runs or background runtime execution from `POST /api/tasks`.
- Do not implement the detailed task type and difficulty keyword rules from the later TaskState initialization milestone.
- Do not interrupt running workers, normalize worker timeout errors, or enforce scheduler cancellation guards.
- Do not change Router v1 schema files, TypeScript declarations, database migrations, or worker contracts.
- Do not add a full conversation/message schema in this change.

## Decisions

### Use `TaskService` as the transaction boundary

`TaskService` will own create, read, append-message, and cancel operations. The API layer should validate HTTP request/response shapes, construct the service with the request settings/session, translate known service/repository errors into HTTP statuses, and avoid duplicating state mutation logic.

Alternatives considered:

- Put logic directly in `backend/app/api/tasks.py`: simpler initially, but it would make service-level tests and future runtime integration harder.
- Add repository methods for every workflow: too low-level because task creation spans task state, artifact store, and event log.

### Create task state before artifact and event side effects

`create_task` should persist the initial `TaskState` first, then write the `raw_user_request` artifact, then append `task.created`.

```
TaskService.create_task
  ├─ TaskRepository.create_task(initial_state)
  ├─ ArtifactStore.write_artifact_content(raw_user_request)
  ├─ EventService.append_event(task.created)
  └─ TaskRepository.get_task(task_id)
```

This order works with existing service behavior:

- `ArtifactStore.write_artifact_content` requires the task to exist.
- `ArtifactStore` updates `current_artifacts` after artifact creation.
- `EventService.append_event` assigns the next per-task sequence and updates `event_seq`.

The service should avoid updating an older `TaskState` snapshot after artifact or event writes, because that can overwrite `current_artifacts` or `event_seq`.

### Keep initial classification conservative

The initial `TaskState` should be valid but intentionally light:

- `status="created"`
- `phase="intake"`
- `task_type="unknown"` unless a very low-risk direct mapping is already available
- low-confidence `difficulty`
- `gates.test_required=false`
- `gates.formal_required=false`
- default runtime limits compatible with existing model constraints

The later TaskState initialization milestone can replace or enrich these defaults with keyword rules.

### Store follow-up user messages as artifacts

`POST /api/tasks/{task_id}/messages` should persist the message as an artifact, likely `type="misc"` with metadata tags such as `user_message`, then emit `task.updated` with the message artifact ID in correlation/payload.

This avoids inventing a conversation table or changing Router schemas before the runtime knows how it will consume user messages. Runtime resume behavior can later query the artifact list or follow event correlation.

### Make cancellation idempotent for already-cancelled tasks only

Cancellation should update cancellable tasks to:

- `status="cancelled"`
- `phase="completed"`
- `completed_at=<now>`
- `updated_at=<now>`

and append `task.cancelled`.

If the task is already `cancelled`, return the current state without appending another event. If the task is in another terminal state (`succeeded`, `partial_failed`, or `failed`), return a conflict instead of rewriting history.

### Add small ID and time helpers

Use `backend/app/core/ids.py` for generated task, session, artifact, and event IDs, and `backend/app/core/time.py` for timezone-aware UTC timestamps. This keeps ID prefixes and clock handling consistent without changing persistence contracts.

## Risks / Trade-offs

- [Risk] Artifact content is written before the database transaction commits, so a later transaction failure can leave an orphan local file. → Mitigation: rely on existing `ArtifactStore` cleanup for metadata write failures and keep task creation operations ordered to minimize post-artifact failures; orphan cleanup can be addressed in a later maintenance task if needed.
- [Risk] The initial `TaskState` may classify tasks too broadly as `unknown`. → Mitigation: this is intentional for the Task API milestone; the next TaskState initialization change owns detailed classification.
- [Risk] Follow-up user messages stored as `misc` artifacts are less semantically rich than a message table. → Mitigation: it preserves current schema boundaries and keeps large/user-provided content externalized; a dedicated conversation model can be added when runtime requirements are clearer.
- [Risk] Cancelled task state does not stop already-running workers. → Mitigation: this change only creates the API state transition; scheduler guards and worker interruption are scoped to later runtime reliability work.

## Migration Plan

No database or schema migration is required. Deploying the change adds new API routes and service code only. Rollback removes the routes and service behavior while leaving existing tasks, artifacts, and events as valid Router v1 records.

## Open Questions

- Should `POST /api/tasks/{task_id}/messages` accept only `message` and optional metadata, or also allow structured project context updates?
- Should `POST /api/tasks` return only the minimal response from `docs/backend.md`, or include the created `TaskState` for frontend convenience?
