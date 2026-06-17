## Context

The backend has already established most of the runtime foundation for Router v1:

- strict Pydantic contracts for `TaskState`, `WorkerInput`, `WorkerResult`, `Artifact`, and `RouterEvent`
- repositories for tasks, artifacts, events, worker jobs, and gate results
- a local Artifact Store
- Scheduler Guard policy checks
- Quality Gate assessment and persistence
- a mock MCP adapter that creates worker jobs, persists produced artifacts, emits worker lifecycle events, and returns validated `WorkerResult` payloads
- a WorkerResult Handler that applies terminal worker results back into persisted `TaskState`

The missing boundary is the Main Agent tool layer. Main Agent should be able to decide which runtime action to request, but Runtime must still enforce dispatch rules, select safe inputs, preserve artifact boundaries, and return compact results.

One important constraint is that task intake classification is not part of this change. The existing Scheduler Guard rejects `created` / `intake` / `unknown` tasks before PLC worker dispatch. The tools introduced here therefore operate on already-classified running tasks. Classification belongs to the later Main Agent service/runtime episode work.

```text
Main Agent
    |
    v
OpenAI function tool wrapper
    |
    v
AgentToolService
    |
    +-- TaskRepository / ArtifactStore
    +-- SchedulerGuard
    +-- WorkerInputBuilder
    +-- McpAdapter
    +-- WorkerResultHandler
    +-- QualityGateService
```

## Goals / Non-Goals

**Goals:**

- Provide function tools for worker calls, parallel worker dispatch, artifact reads, Quality Gate execution, and task finishing.
- Keep the core tool implementation testable without invoking an OpenAI model.
- Centralize `WorkerInput` construction so tests, scripts, and future runtime code do not duplicate worker mode and expected output mappings.
- Enforce Scheduler Guard before worker side effects.
- Keep MCP adapter ownership of worker job creation, produced artifact persistence, and worker lifecycle events.
- Track active worker refs and worker call counters around dispatch so concurrency and budget limits remain accurate.
- Return Main Agent-safe summaries instead of full artifact contents, logs, code, or counterexamples by default.

**Non-Goals:**

- Do not implement Main Agent reasoning, instructions, structured output, or `Runner.run`.
- Do not implement intake classification or automatic conversion from `created/intake/unknown` to `running/planning`.
- Do not add or change Router v1 schema fields.
- Do not add public HTTP API endpoints.
- Do not implement real MCP server/client integration beyond the existing adapter mode.
- Do not generate final report artifacts beyond finishing task state; final report synthesis is a later step.

## Decisions

### Keep SDK wrappers thin

Implement an SDK-independent service or helpers in `backend/app/agents/tools.py`, then expose those helpers through OpenAI Agents SDK `@function_tool` wrappers when the dependency is available.

The SDK wrapper should only translate the Agents SDK context into the local tool context and call the same service used by unit tests and scripts. This avoids coupling runtime behavior to a live LLM run.

Alternative considered: put all behavior directly inside decorated functions. Rejected because the tool behavior would be harder to test without the SDK and harder to reuse from development scripts.

### Add a dedicated WorkerInputBuilder

Create `backend/app/workers/worker_input_builder.py` for deterministic `WorkerInput` construction. It should:

- select input artifacts from `TaskState.current_artifacts`
- generate worker job IDs and idempotency keys
- map `WorkerType` to `WorkerMode`
- map `WorkerType` to expected output artifact types
- populate `WorkerContext` from normalized goal, task type, difficulty, project context, assumptions, repair round, and selected open failure IDs
- build budget and trace context

Alternative considered: keep worker input construction inside `tools.py`. Rejected because the same logic is already duplicated in tests and development scripts, and future runtime loop code will need it too.

### Let Scheduler Guard run before side effects

Each worker tool should load the latest task, select proposed input artifacts, and call Scheduler Guard before updating task active job state or invoking the adapter. Guard violations should return a structured rejected tool result and must not mutate task state.

For parallel dispatch, the tool should build all proposed jobs first, call `validate_parallel_jobs`, and only then perform any dispatch side effects.

Alternative considered: rely on `WorkerInput` model validation and adapter errors. Rejected because Scheduler Guard owns runtime policy such as classification state, repair eligibility, concurrency, and finish readiness.

### Do not duplicate adapter-owned side effects

`McpAdapter.call_worker()` already creates the `worker_jobs` row, emits `worker.started`, persists produced artifacts, completes the worker job, and emits terminal worker events. The tool layer should not repeat those writes.

The tool layer may update `TaskState.active_worker_jobs`, `runtime_limits.active_parallel_workers`, and `runtime_limits.worker_calls_used` before calling the adapter. After the handler applies the result, the tool layer should ensure active worker counters are decremented even on normalized timeout/error results.

Alternative considered: have tools create worker jobs and emit worker events before calling the adapter, matching the old prose in `docs/backend.md`. Rejected because it conflicts with current adapter ownership and can create duplicate events or repository conflicts.

### Return compact tool results

Every tool should return a small structured result shaped for Main Agent consumption:

- `status`: `applied`, `rejected`, `failed`, or equivalent stable value
- `summary`
- `artifact_refs`
- `failures`
- `gate_state`
- `next_recommended_action`
- optional `error` or `violation` details

The result should not include full PLC code, full test logs, formal counterexample bodies, or full worker logs. `read_artifact` may expose `summary` mode and bounded `full` mode with truncation metadata.

Alternative considered: return raw `WorkerResult` or full artifact payloads. Rejected because the project principle is that large content remains artifactized and Main Agent context stays compact.

### Treat finish_task as guarded state transition

`finish_task` should call `validate_finish_task` for successful completion. If the guard accepts, it should persist terminal task state and emit the corresponding task terminal event. If the guard rejects, it should return a rejected tool result without mutation.

For non-success terminal outcomes, the initial implementation may support conservative `failed` or `partial_failed` completion without requiring Quality Gate success, while still preserving completed timestamps and terminal events.

Alternative considered: make `finish_task` only return a recommendation and leave mutation to Main Agent Service. Rejected because the tool exists to give Runtime a single audited terminal transition.

## Risks / Trade-offs

- [Risk] Tool state updates and WorkerResult Handler both touch task worker tracking. -> Mitigation: keep Handler responsible for removing active job refs and completed IDs, while tool code owns pre-dispatch active refs and runtime counters.
- [Risk] A worker call may fail before the adapter returns a normalized `WorkerResult`. -> Mitigation: wrap dispatch so active counters are cleaned up or the task is returned to a consistent state before re-raising or returning an error result.
- [Risk] Parallel dispatch with a synchronous adapter is not truly concurrent. -> Mitigation: make v1 semantics a guarded batch that can run sequentially in tests; preserve the public tool shape for later async/concurrent execution.
- [Risk] `read_artifact(full)` can leak large content into Main Agent context. -> Mitigation: require bounded output and truncation metadata.
- [Risk] Adding `openai-agents` can make tests depend on an external SDK import. -> Mitigation: keep core service SDK-independent and isolate SDK decorators behind a small import boundary or optional wrapper.

## Migration Plan

No data migration is required. Implementation can be delivered behind unit tests and local scripts:

1. Add the worker input builder and tool service.
2. Add SDK wrappers if the dependency is introduced.
3. Add unit coverage using sqlite-backed repositories and mock adapter mode.
4. Add `scripts/dev_call_agent_tool.py` for local inspection.
5. Existing APIs and schemas continue to work without change.

Rollback is straightforward: remove the new tool module, builder, tests, script, and dependency if added. No persisted data shape changes are involved.

## Open Questions

- Should `finish_task` create a `final_report` artifact in this change, or should it only mark terminal state and leave final report generation to the later report synthesis step? The safer v1 default is terminal state only.
- Should SDK wrappers be implemented in the same `tools.py` file or split into `tools.py` for core behavior and a small `sdk_tools.py` wrapper file? The current proposal names `tools.py`, but a split may keep optional dependency boundaries cleaner.
