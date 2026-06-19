## Context

Router already has most of the raw trace material:

- `TaskState.trace` stores `openai_trace_id`, `main_agent_run_ids`, and `latest_main_agent_run_id`.
- `TraceContext` travels through `WorkerInput` and `WorkerResult`.
- `RouterEvent.correlation` supports `openai_trace_id`, `main_agent_run_id`, `worker_job_id`, `mcp_request_id`, `artifact_ids`, and `failure_ids`.
- Main Agent observability persists turn/tool/completed events plus final report and replay log artifacts.
- The real MCP path assigns an `mcp_request_id` and persists worker job input/result JSON.
- Artifacts store creator and derivation metadata such as worker job ID and Main Agent run ID.

The gap is not the absence of IDs. The gap is that those IDs are spread across task state, events, worker job rows, artifact rows, and gate result rows, and several event writers only populate part of the correlation object. For example, worker lifecycle events currently know the worker job and MCP request IDs, but not always the Main Agent run and trace IDs that led to the dispatch. Quality Gate and task lifecycle events can also be correlated more completely when the task trace is known.

There is also an active design for an OpenAI-compatible Main Agent runner where external SDK tracing may be disabled. This change must therefore treat `openai_trace_id` as Router's internal trace correlation ID first, and only as an external OpenAI SDK trace ID when the selected provider supports it.

```text
task_id
  |
  +-- TaskState.trace
  |     +-- openai_trace_id
  |     +-- main_agent_run_ids[]
  |
  +-- Router events
  |     +-- main_agent events
  |     +-- worker events
  |     +-- artifact events
  |     +-- gate events
  |     +-- terminal task events
  |
  +-- Worker jobs
  |     +-- WorkerInput.trace_context
  |     +-- WorkerResult.trace_context
  |
  +-- Artifacts
  |     +-- created_by
  |     +-- derived_from_worker_job_id
  |
  +-- Gate results
        +-- evidence_artifact_ids
```

## Goals / Non-Goals

**Goals:**

- Provide a deterministic task-scoped trace summary that can be read without manual joins.
- Preserve trace mapping when external OpenAI SDK trace export is disabled, unsupported, or not configured.
- Fill existing `RouterEvent.correlation` fields consistently at event creation boundaries.
- Keep large content in artifacts and only return compact metadata, IDs, status, sequence numbers, summaries, and references.
- Add structured runtime log context for debugging without logging secrets, raw model payloads, full code, full reports, or full artifact contents.
- Keep the implementation compatible with the existing Router v1 model, JSON Schema, and TypeScript declarations.

**Non-Goals:**

- Do not add a trace database table in the first implementation.
- Do not change Router v1 schema field names or enum values.
- Do not require external OpenAI trace export or OpenAI dashboard availability.
- Do not expose hidden model reasoning, raw SDK event objects, or internal artifact contents through the trace summary.
- Do not build a frontend trace visualization in this change.
- Do not implement the OpenAI-compatible Main Agent runner in this change; only preserve a trace contract that remains valid for it.

## Decisions

### Build a read-only task trace projection instead of a new table

Implement a small trace summary service that reads existing persisted data by `task_id`:

- `TaskRepository.get_task`
- `EventRepository.list_events`
- `WorkerJobRepository` task-scoped listing or equivalent query
- `ArtifactRepository.list_task_artifacts`
- `GateResultRepository` task-scoped listing or equivalent query

The service returns a compact projection suitable for an API response. The projection should not become the write source of truth; it is reconstructed from durable Router state.

Alternative considered: add a `trace_edges` or `trace_map` table and write to it during every runtime action. Rejected for the first version because the existing persisted rows already contain enough correlation data for task-scoped lookup, and a new write path would add transactional risk across many services.

### Add a dedicated trace endpoint

Expose the projection through a read-only endpoint such as:

```text
GET /api/tasks/{task_id}/trace
```

This avoids changing the existing `GET /api/tasks/{task_id}` response model, which currently returns `TaskState` and is tied to the Router v1 contract. The trace endpoint can evolve as a query projection while keeping existing task API consumers stable.

Alternative considered: embed a larger `trace_summary` object inside `TaskState`. Rejected because it would expand the core Router contract and duplicate data that can be projected from persisted rows.

### Treat `openai_trace_id` as Router's internal trace correlation ID

Keep the existing field name for contract compatibility, but define behavior operationally: Router always persists an internal trace ID for Main Agent episodes. When the official OpenAI Agents SDK path supports tracing, that value may also be passed as the SDK trace ID. When compatible providers disable external trace export, the same field still links Router events, worker jobs, artifacts, and logs.

Alternative considered: add a new `router_trace_id` field immediately. Rejected because the current schema and tests already use `openai_trace_id`; adding a new field would require a schema migration without providing immediate value.

### Fill event correlation at write boundaries

Do not run a backfill or infer missing event correlation later. Instead, update the services that already own event creation:

- Main Agent events continue using existing run and trace IDs.
- Worker and artifact events copy `openai_trace_id` and `main_agent_run_id` from `WorkerInput.trace_context`.
- Real MCP worker events include `mcp_request_id` when assigned.
- Quality Gate events read the current task trace before appending gate events.
- Task cancellation and terminal task events include task trace when available.

Alternative considered: have the trace summary service infer all missing fields from worker job input JSON. The summary can still do defensive inference for display, but event rows themselves should carry complete correlation whenever the writer knows it.

### Keep trace summary bounded and content-free

The trace summary should expose references and metadata:

- IDs
- event sequence and type
- source type
- status and timestamps
- artifact type, version, visibility, summary, and URI/hash metadata
- worker type, job status, MCP request ID, execution status, and produced artifact IDs
- gate result status and evidence artifact IDs

It should not include full artifact content, full logs, full PLC code, full reports, raw model outputs, raw SDK event payloads, or hidden chain-of-thought.

Alternative considered: include replay log content inline for a one-call debug API. Rejected because replay logs can grow and may be internal visibility artifacts.

### Make logs contextual but secondary

Add helpers or adapters in the logging layer so runtime services can log with consistent context. Logs are for operational diagnosis, while Router events/artifacts/worker jobs remain the durable audit source. Logging should redact secret-like keys and avoid serializing large bodies.

Alternative considered: rely only on logs for trace reconstruction. Rejected because logs are deployment-dependent, may be sampled or rotated, and are not part of the Router persistence contract.

## Risks / Trade-offs

- [Risk] Trace summary projection may become slow for tasks with many events or artifacts. -> Mitigation: query by `task_id`, keep the response compact, add limits or pagination later if real traces become large.
- [Risk] Existing historical tasks will have sparse event correlation. -> Mitigation: projection can still join by `task_id` and worker job input/result JSON, while new writes carry fuller event correlation.
- [Risk] `openai_trace_id` naming is misleading when SDK tracing is disabled. -> Mitigation: document the operational meaning and avoid adding a schema migration until a broader contract versioning decision is needed.
- [Risk] Logs can accidentally include secrets or large content if callers pass raw payloads. -> Mitigation: centralize context formatting/redaction and add tests that assert API keys, tokens, database passwords, code bodies, and artifact contents are omitted.
- [Risk] Adding a trace endpoint may expose internal-only details. -> Mitigation: return compact metadata and honor artifact/event visibility boundaries; do not inline artifact or replay log contents.

## Migration Plan

No database migration is required for the first version.

Implementation can proceed in small steps:

1. Add read-only repository helpers for task-scoped worker jobs and gate results if missing.
2. Add trace summary Pydantic response models or service-local DTOs.
3. Implement the trace summary service using existing repositories.
4. Add `GET /api/tasks/{task_id}/trace`.
5. Fill event correlation fields in worker, artifact, gate, cancel, and terminal task event writers.
6. Add correlated logging helpers and apply them at high-value runtime boundaries.
7. Add unit and integration tests for summary shape, correlation propagation, redaction, and bounded output.

Rollback can remove the endpoint, trace summary service, and logging helpers. The event correlation additions are backward-compatible and do not require rollback for existing clients.

## Open Questions

- Should `GET /api/tasks/{task_id}/trace` expose only user-visible events by default, or provide an authenticated/internal flag for internal event summaries later?
- Should `main_agent_log` internal artifact IDs appear in trace summaries when referenced by user-visible completed events, or should the summary only expose the user-visible final report artifact?
- Should trace summary response models live in `backend/app/api/tasks.py` initially or in a dedicated `backend/app/schemas/trace_summary.py` module?
