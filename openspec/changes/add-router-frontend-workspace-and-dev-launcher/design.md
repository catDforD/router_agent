## Context

The backend already exposes the frontend-facing Router workflow through FastAPI endpoints documented in `docs/frontend-api.md`: task creation, current `TaskState`, user follow-up messages, cancellation, task SSE, artifact metadata/content, trace summary, and health checks. The Router v1 TypeScript reference contract exists at `schema/ts/router_contract.d.ts`, while the actual `frontend/` tree is an empty scaffold.

The backend execution model is event and artifact oriented. A browser client should not call worker/MCP internals or expect large PLC code, reports, patches, replay logs, or final reports inside `TaskState`. It should subscribe to task events, periodically refresh compact projections, and lazy-load artifact content.

Local development currently requires several separate commands: database setup, backend API startup, optional worker MCP startup, and future frontend startup. The requested developer experience is a single command shaped like `uv run main.py` that starts the local stack and prints process and endpoint information.

## Goals / Non-Goals

**Goals:**

- Implement a usable single-task Router frontend workspace as the first screen.
- Consume only the documented Router backend public endpoints.
- Keep Router v1 payload types aligned with `schema/ts/router_contract.d.ts` rather than hand-copying schemas.
- Use SSE as the live event source with deterministic resume via `after_seq`.
- Render task status, phase, worker progress, quality gates, repair rounds, artifacts, final report, clarification prompts, cancellation, and trace/debug projections.
- Add a local launcher command shaped like `uv run main.py` that starts or verifies local services, starts backend and frontend processes, prefixes logs, handles shutdown, and prints access URLs.
- Preserve existing backend API behavior and schema contracts.

**Non-Goals:**

- Do not add authentication, user accounts, task history, or multi-user collaboration.
- Do not add new backend endpoints unless a later change identifies a real API gap.
- Do not expose internal worker MCP APIs, Main Agent hidden reasoning, raw SDK traces, or internal-only artifacts in the default user UI.
- Do not replace existing database setup documentation; the launcher should build on it.
- Do not package a production frontend deployment in this change.

## Decisions

### Build a task workspace rather than a landing page

The first frontend screen should be the Router task workspace: prompt entry, task status, execution timeline, worker/gate cards, artifacts, final report, and trace drawer.

```text
┌────────────────────────────────────────────────────────────┐
│ Task Header: title/status/phase/gates/connection/cancel     │
├───────────────┬───────────────────────────┬────────────────┤
│ Chat Panel    │ Execution Workspace        │ Artifact Panel │
│ create/reply  │ timeline + agent cards     │ code/reports   │
│ clarification │ gates + repair rounds      │ final report   │
└───────────────┴───────────────────────────┴────────────────┘
```

Alternative considered: create a documentation-style page that explains the workflow. Rejected because the backend already supports an executable task lifecycle; the frontend should make the system usable rather than describe it.

### Keep frontend state as projections over events, task state, artifacts, and trace

The frontend should maintain separate state slices:

```text
eventLog: RouterEvent[]       append-only SSE frames
taskState: TaskState | null   fetched after creation and key events
artifacts: Artifact[]         refreshed after artifact/terminal events
artifactContent cache         loaded when selected
traceSummary                  loaded for debug/replay views
```

SSE events drive live rendering and cursor tracking. `TaskState` remains the authoritative compact status projection. Artifact lists and content are lazy-loaded. Trace summary is a debug/timeline reconstruction aid.

Alternative considered: mutate a full task view only from SSE payloads. Rejected because events are compact, payload shapes vary by event type, and the backend already exposes `TaskState`, artifact, and trace projections for deterministic refresh.

### Use existing Router API helpers with a Vite proxy

Frontend API modules should map one-to-one to existing endpoints:

- `tasks.ts`: create task, get task, append user message, cancel task
- `events.ts`: open/resume task SSE stream
- `artifacts.ts`: list artifacts, read artifact content
- `trace.ts`: get task trace summary
- `client.ts`: shared base URL, JSON/error handling, health checks

Local frontend development should call relative `/api/...` paths through a Vite dev-server proxy to `http://127.0.0.1:8000`. This avoids requiring backend CORS changes for the MVP. The UI can still accept an explicit API base URL for non-proxy use later.

Alternative considered: add CORS middleware now. Rejected for this change because proxying keeps the frontend implementation isolated from backend API behavior. CORS can be added in a separate backend change if needed for non-proxy deployments.

### Render artifacts according to artifact type and MIME type

Artifact metadata should populate the artifact panel immediately, while content should load only on selection or final-report rendering. JSON artifacts should be parsed for structured display when possible; text artifacts should render as preformatted/code/report views. Non-UTF-8 responses should surface metadata plus a clear unsupported preview state.

Alternative considered: eagerly fetch all artifact content after every artifact event. Rejected because the architecture intentionally keeps large content behind artifact references.

### Treat final report as the completed task summary source

After `main_agent.completed` or a terminal task event, the frontend should find the `final_report` artifact from `TaskState.current_artifacts.final_report`, artifact list, or event payload, then fetch and render it as the main completion view.

Alternative considered: synthesize final summaries from events and task state. Rejected because the backend already persists a stable final report payload with delivery artifacts, validation evidence, repairs, assumptions, unresolved items, and trace refs.

### Implement a root Python dev launcher

Add a root `main.py` intended to run as:

```bash
uv run main.py
```

The launcher should:

- load local environment from `.env` when present;
- ensure the artifact root exists;
- optionally start PostgreSQL through the existing Docker Compose service, or verify an externally managed database is reachable;
- run database migrations before backend startup unless disabled;
- start the backend API with the current Python environment;
- start the frontend dev server through the configured Node package manager;
- start the local PLC worker MCP server when configuration requires a real worker path or a launcher flag requests it;
- prefix child-process logs with process names;
- print a startup table with process name, PID, port, status, and command;
- print access URLs for frontend, backend health, OpenAPI docs, task API base, SSE URL pattern, and MCP worker URL when applicable;
- terminate child processes cleanly on Ctrl-C.

PostgreSQL started via Docker Compose can remain running after shutdown unless an explicit cleanup flag is provided, because it is a developer service with durable local state.

Alternative considered: implement shell scripts only. Rejected because process supervision, readiness checks, cross-platform path handling, URL output, and clean shutdown are easier to keep coherent in a Python launcher.

### Keep package setup explicit but friendly

The frontend should use Vite + React + TypeScript. The launcher should detect missing frontend dependencies and either run the documented install command when an install flag is supplied or fail with a concise message that includes the command to run. It should not silently modify dependency state unless explicitly configured.

Alternative considered: automatically install frontend dependencies on every launch. Rejected because surprise package-manager writes make local debugging harder and can obscure dependency failures.

## Risks / Trade-offs

- [Risk] SSE reconnection can duplicate events if the frontend cursor is not tracked correctly. -> Mitigation: store the largest observed `seq`, de-duplicate by `event_id`/`seq`, and reconnect with `after_seq`.
- [Risk] Frontend schemas can drift from Router v1. -> Mitigation: derive/import types from `schema/ts/router_contract.d.ts` or keep a thin generated copy with a documented refresh path.
- [Risk] Large artifacts can make the UI slow if fetched eagerly. -> Mitigation: lazy-load artifact content and cache per artifact ID/content hash.
- [Risk] The launcher can mask missing database or dependency setup. -> Mitigation: use explicit readiness checks and print actionable failures before reporting the stack as ready.
- [Risk] Starting Docker-managed PostgreSQL by default can conflict with a locally installed PostgreSQL on port 5432. -> Mitigation: provide flags to skip Docker PostgreSQL, detect occupied ports, and document behavior.
- [Risk] Optional real MCP worker startup can require secrets or long-running provider calls. -> Mitigation: only start the worker server when configuration or flags request it, redact secrets, and print worker mode in the startup summary.

## Migration Plan

1. Add frontend package metadata, Vite configuration, TypeScript configuration, React entrypoint, global styles, API helpers, hooks, and task workspace components.
2. Add the root dev launcher and document its flags, readiness behavior, process output, and shutdown behavior.
3. Update local development docs to include both the existing manual path and the new one-command path.
4. Validate backend compatibility with the existing backend tests and targeted frontend/runtime smoke checks.
5. Rollback can remove the frontend implementation and launcher without database migrations or Router v1 schema changes.

## Open Questions

- Should `uv run main.py` start Docker Compose PostgreSQL by default, or only when `--with-postgres` is passed?
- Should the launcher start the local PLC MCP server automatically for `MCP_MODE=real` and `MCP_MODE=hybrid`, or require an explicit `--with-worker` flag?
- Should the first frontend implementation use only local component state, or add TanStack Query for task/artifact/trace cache management from the start?
