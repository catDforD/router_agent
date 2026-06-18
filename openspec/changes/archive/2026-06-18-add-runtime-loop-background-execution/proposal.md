## Why

Task creation currently persists a durable `created/intake/unknown` task, but Main Agent execution still has to be triggered manually from tests or scripts. The next backend milestone needs `POST /api/tasks` to return quickly while Runtime continues the Main Agent episode in the background and streams progress through existing events.

## What Changes

- Add a Runtime Loop service boundary that can start and resume one Main Agent episode for a task.
- Schedule background Runtime execution after successful task creation without blocking the HTTP response.
- Schedule Runtime resume after a user follow-up message is accepted for a non-terminal task.
- Add a lightweight runtime episode lease so duplicate HTTP requests, retries, or resume triggers do not run overlapping Main Agent episodes for the same task.
- Add checkpoint commits around visible runtime progress so `GET /api/tasks/{task_id}` and `GET /api/tasks/{task_id}/events` can observe background execution while it is running.
- Harden cancellation semantics so a cancelled task is not later overwritten by a Main Agent finish action or new worker dispatch.
- Keep the first version in-process using FastAPI `BackgroundTasks`; durable external queues such as Celery or Dramatiq remain future work.

## Capabilities

### New Capabilities
- `runtime-loop-background-execution`: Runs and resumes Main Agent task episodes in the background after frontend task API mutations while preserving runtime policy, event visibility, cancellation safety, and single-episode execution.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `backend/app/services/runtime_service.py`
  - `backend/app/api/tasks.py`
  - `backend/app/main.py` or app state wiring if a runtime scheduler dependency is needed
  - `backend/app/agents/main_agent.py`
  - `backend/app/agents/tools.py`
  - `backend/app/services/task_service.py`
  - tests under `backend/app/tests/unit/` and `backend/app/tests/integration/`
- Existing public HTTP request and response schemas remain unchanged.
- Existing Router v1 Pydantic models, JSON Schema files, TypeScript declarations, and database tables remain unchanged for the MVP.
- Existing mock worker, Scheduler Guard, Quality Gate, Artifact Store, Event Service, and Main Agent runner behavior are reused.
