## Context

The backend already has the deterministic execution pieces for a mock Router flow: persisted tasks/artifacts/events/worker jobs, Scheduler Guard, Quality Gate, mock MCP workers, WorkerResult Handler, Main Agent tools, and `MainAgentService.run_episode(task_id)`. The missing boundary is `runtime_service.py`: task creation currently commits `task.created` and returns `status: "created"`, while Main Agent episodes are only started manually from tests or local scripts.

`MainAgentService.run_episode` is synchronous today. It can run intake classification and orchestration, and tool calls mutate state and append events through the current SQLAlchemy session. Because event writes are only visible to SSE readers after commit, a background Runtime Loop must create progress checkpoints instead of committing only after the full episode completes.

```text
POST /api/tasks
    |
    | create task + raw artifact + task.created
    | commit request transaction
    v
201 task handle
    |
    | FastAPI BackgroundTasks
    v
RuntimeService.start_task(task_id)
    |
    | claim runtime episode lease
    v
MainAgentService.run_episode(task_id)
    |
    +--> checkpoint visible progress
    +--> stop on waiting_user / terminal / cancelled
    +--> release runtime lease
```

The active `add-openai-compatible-main-agent-runner` change remains separate. Runtime should call the configured Main Agent service boundary and should not duplicate provider-specific runner behavior.

## Goals / Non-Goals

**Goals:**

- Start Main Agent execution in the background after successful `POST /api/tasks`.
- Resume Main Agent execution in the background after accepted user follow-up messages.
- Keep task API responses fast and preserve existing response schemas.
- Use a separate background database session rather than the request-scoped session.
- Prevent overlapping Runtime episodes for one task.
- Commit checkpoints so `GET /api/tasks/{task_id}` and SSE event streams can observe execution progress.
- Preserve cancellation safety: after cancellation, Runtime must not start new workers or overwrite the cancelled task with success.
- Keep tests deterministic with injectable fake runners and mock workers.

**Non-Goals:**

- Do not add Celery, Dramatiq, Redis, or another durable queue in the MVP.
- Do not change Router v1 Pydantic models, JSON Schema files, TypeScript declarations, or database tables.
- Do not implement real MCP worker execution.
- Do not guarantee hard interruption of an already-running synchronous worker call.
- Do not synthesize final report artifacts.
- Do not merge provider compatibility runner work into this change.

## Decisions

### Use FastAPI BackgroundTasks for the first Runtime scheduler

Task API handlers should add a background job only after the request transaction commits successfully. The background job should call a small Runtime entrypoint with `task_id` and process settings, then open its own database session.

```text
create_task handler
    create task
    commit
    background_tasks.add_task(runtime_start_task, task_id, settings)
    return 201
```

Alternative considered: introduce Celery or Dramatiq immediately. Rejected for MVP because current workers are mock/in-process and the main gap is service wiring, not distributed queue reliability. A queue can replace the scheduler boundary later without changing Main Agent tools or Router contracts.

### Make RuntimeService the only background orchestration boundary

`backend/app/services/runtime_service.py` should expose the planned methods:

- `start_task(task_id)`
- `run_main_agent_episode(task_id)`
- `resume_after_user_message(task_id)`

The API layer should schedule these methods but should not own runtime decisions. `RuntimeService` should construct `MainAgentService` using settings (`artifact_root`, `mcp_mode`, `mock_scenario`, `main_agent_model`, `main_agent_max_turns`) and an optional injected runner for tests.

Alternative considered: call `MainAgentService.run_episode` directly from `tasks.py`. Rejected because session management, duplicate-run prevention, resume behavior, and runtime error recording need a dedicated boundary.

### Store an MVP episode lease in TaskState metadata

To avoid overlapping Main Agent episodes for the same task, Runtime should claim a lightweight lease before running. For the MVP, store this under `TaskState.metadata["runtime"]` while updating the task row under a database row lock.

Example metadata shape:

```json
{
  "runtime": {
    "episode_status": "running",
    "episode_id": "runtime-episode-...",
    "lease_owner": "in-process",
    "lease_until": "2026-06-18T12:00:00Z",
    "started_at": "2026-06-18T11:55:00Z",
    "last_error": null
  }
}
```

Runtime should skip execution when the task is terminal, waiting on unresolved user input, or already has a non-expired running lease. Runtime should release or mark the lease idle when an episode returns, fails safely, reaches a terminal state, or pauses for user clarification.

Alternative considered: add a `runtime_runs` table. Rejected for the MVP because the current schema can represent enough transient lease state in metadata and the proposal explicitly avoids database migrations. A table is a better later fit for cross-process queues and historical runtime run audit.

### Add checkpoint commits without moving commits into low-level services

`MainAgentService` and `AgentToolContext` should accept an optional checkpoint callback. Runtime supplies `session.commit` as that callback; tests and direct scripts can omit it. The callback should run after meaningful visible progress:

- `main_agent.started`
- intake classification applied or clarification requested
- worker dispatch started
- worker result handled and worker terminal event appended
- Quality Gate completed
- task terminal event appended
- Runtime or Main Agent failure event appended

This keeps low-level services testable and transaction-agnostic while allowing background execution to publish progress to other database sessions.

Alternative considered: commit only after `run_episode` returns. Rejected because the event streaming requirement needs visible progress while the background task is still running.

### Treat cancellation as cooperative and terminal-state authoritative

Cancellation already marks cancellable tasks as `cancelled`. Runtime and tools should make that terminal state authoritative:

- `RuntimeService` checks the latest task before starting or resuming.
- worker tools already load current task before dispatch, so Scheduler Guard should continue rejecting terminal tasks before new worker side effects.
- `finish_task` should reject terminal or cancelled tasks instead of overwriting them.
- Runtime should re-check task status after checkpoints and before releasing or continuing a resumed episode.

MVP cancellation is cooperative. It prevents additional worker dispatch and terminal overwrite after cancellation, but it does not forcibly kill a worker call that is already executing synchronously in-process.

Alternative considered: implement hard cancellation of running workers. Rejected because mock worker calls are synchronous and real worker cancellation belongs with the future durable worker execution model.

### Resume after user message by answering open clarification questions

When `POST /api/tasks/{task_id}/messages` succeeds, the API should schedule `RuntimeService.resume_after_user_message(task_id)`. If the task is `waiting_user`, Runtime should load the latest user message artifact, mark open required clarification questions as answered with a compact answer reference or text, move the task back to `running/planning`, emit `task.updated`, checkpoint, and then run a new Main Agent episode.

If the task is no longer waiting or is terminal by the time the background job starts, Runtime should no-op safely.

Alternative considered: leave questions open and let Main Agent infer the answer from artifacts. Rejected because Scheduler Guard rejects worker dispatch while required clarification questions remain open.

### Record runtime failures without false success

Unhandled Runtime errors should be observable through a user-visible error event correlated with the task where possible, then the lease should be released with `last_error` metadata. Runtime should not mark the task `succeeded` because of a background exception. For provider/model behavior failures already handled by `MainAgentService`, Runtime should checkpoint the emitted Main Agent failure event and leave the task resumable unless existing service behavior marks it terminal.

Alternative considered: mark any background exception as `failed`. Rejected for MVP because provider/network failures can be transient and a resumable task is safer than prematurely terminal state.

## Risks / Trade-offs

- [Risk] In-process BackgroundTasks are not durable across process crashes. -> Mitigation: keep RuntimeService boundaries queue-agnostic so a durable queue can replace scheduling later.
- [Risk] Metadata leases are weaker than a dedicated runtime run table. -> Mitigation: use row-level locking and short lease TTLs; migrate to a table when cross-process reliability becomes necessary.
- [Risk] Checkpoint commits can expose partial progress. -> Mitigation: Router state and events already model progress as append-only artifacts/events, and terminal success still requires Quality Gate and Scheduler Guard.
- [Risk] Cooperative cancellation cannot stop a worker already executing synchronously. -> Mitigation: document the MVP semantics and enforce no new worker starts or terminal overwrite after cancellation.
- [Risk] Resume may map a free-form user message to all open clarification questions. -> Mitigation: store the raw message as an artifact, keep the answer compact, and let the next Main Agent episode refine state before dispatch.

## Migration Plan

No database or Router contract migration is required.

Implementation can roll out behind tests:

1. Add RuntimeService with explicit session ownership, episode lease helpers, and injectable runner/scheduler hooks.
2. Add checkpoint callback plumbing to MainAgentService and Main Agent tool context.
3. Wire `POST /api/tasks` and `POST /api/tasks/{task_id}/messages` to schedule background Runtime after successful commits.
4. Harden terminal/cancelled finish behavior.
5. Add integration coverage for asynchronous create, progress visibility, cancellation, and resume.

Rollback removes the RuntimeService scheduling calls and checkpoint plumbing. Existing synchronous MainAgentService tests, task API behavior, mock workers, and event streaming remain usable.

## Open Questions

- Should runtime lease metadata include a process identifier or hostname now, or wait until multi-process scheduling exists?
- Should Runtime mark repeated background failures as `failed` after a threshold, or remain resumable until a later reliability change?
- Should structured clarification answers get a dedicated future API shape instead of mapping a free-form message to open questions?
