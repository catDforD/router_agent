## 1. Runtime Service Boundary

- [x] 1.1 Implement `RuntimeService` construction in `backend/app/services/runtime_service.py` with explicit settings, session factory, artifact root, MCP mode, mock scenario, model, max turns, and optional injected Main Agent runner.
- [x] 1.2 Add runtime task-state helpers for terminal detection, runnable-state detection, metadata merge/update, and runtime episode ID generation.
- [x] 1.3 Implement runtime episode lease claim/release helpers using `TaskState.metadata["runtime"]`, row-level task locking where available, lease TTL handling, and safe no-op behavior for non-runnable tasks.
- [x] 1.4 Implement `start_task(task_id)`, `run_main_agent_episode(task_id)`, and `resume_after_user_message(task_id)` with background-owned database sessions, commit/rollback handling, and observable error recording.
- [x] 1.5 Add unit tests for terminal no-op, waiting-user no-op on start, duplicate non-expired lease skip, expired lease reclaim, lease release after pause/terminal completion, and runtime exception metadata.

## 2. Checkpointing And Cancellation Safety

- [x] 2.1 Add an optional checkpoint callback to `MainAgentService` and `AgentToolContext` without changing existing direct-call behavior when no callback is provided.
- [x] 2.2 Invoke checkpoints after `main_agent.started`, intake classification or clarification pause, worker dispatch start, worker terminal handling, Quality Gate completion, task terminal events, and Main Agent error events.
- [x] 2.3 Harden terminal-state handling so `finish_task` rejects attempts to overwrite `cancelled`, `succeeded`, `partial_failed`, or `failed` tasks.
- [x] 2.4 Add focused unit tests proving checkpoints are called at meaningful milestones and cancelled tasks cannot be overwritten by stale finish attempts.

## 3. Task API Background Scheduling

- [x] 3.1 Add a small scheduler helper or dependency that wraps FastAPI `BackgroundTasks` and calls RuntimeService entrypoints after successful request commits.
- [x] 3.2 Wire `POST /api/tasks` to schedule `RuntimeService.start_task(task_id)` after `TaskService.create_task` commits, while preserving the existing response model and response body.
- [x] 3.3 Wire `POST /api/tasks/{task_id}/messages` to schedule `RuntimeService.resume_after_user_message(task_id)` after `TaskService.append_user_message` commits.
- [x] 3.4 Add API tests proving create/message responses keep their existing shape, runtime scheduling happens only after successful commits, and failed request paths do not schedule runtime work.

## 4. Clarification Resume Behavior

- [x] 4.1 Add a helper that finds the latest persisted user message artifact for a task and returns compact answer context suitable for `ClarificationQuestion.answer`.
- [x] 4.2 Implement resume behavior that marks open required clarification questions as answered, moves `waiting_user/clarifying` tasks back to `running/planning`, emits `task.updated`, checkpoints, and then starts a new Main Agent episode.
- [x] 4.3 Add unit tests for waiting-user resume, terminal resume no-op, non-waiting resume no-op, missing user message handling, and answered clarification fields.

## 5. Runtime Integration Coverage

- [x] 5.1 Add `backend/app/tests/integration/test_runtime_loop.py` with deterministic fake Main Agent runners and a fake/in-process scheduler hook.
- [x] 5.2 Cover task creation returning quickly while a scheduled background episode later completes the ordinary mock dev/test/gate/finish path as `succeeded`.
- [x] 5.3 Cover progress visibility by asserting committed `main_agent.started`, worker, artifact, gate, and terminal events are observable through a separate session during or after checkpointed background execution.
- [x] 5.4 Cover cancellation safety by cancelling before a scheduled runtime job or stale finish attempt continues, then asserting no later worker job is created and task status remains `cancelled`.
- [x] 5.5 Cover duplicate runtime triggers by starting or resuming the same task twice and asserting only one episode runs while the runtime lease is active.
- [x] 5.6 Cover user-message resume from `waiting_user` through answered clarification and a subsequent Main Agent episode.

## 6. Verification

- [x] 6.1 Run focused Runtime unit tests for lease helpers, resume helpers, checkpoint callback behavior, and cancellation-safe finish behavior.
- [x] 6.2 Run `uv run pytest backend/app/tests/integration/test_runtime_loop.py -q`.
- [x] 6.3 Run existing Main Agent, Task API, Agent Tools, Event API, and Scheduler Guard tests to verify behavior remains compatible.
- [x] 6.4 Run `uv run python -m compileall backend`.
- [x] 6.5 Run `git diff --check`.
