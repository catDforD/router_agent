## 1. Frontend Project Setup

- [x] 1.1 Populate `frontend/package.json` with Vite, React, TypeScript, dev scripts, and required UI/runtime dependencies.
- [x] 1.2 Populate `frontend/tsconfig.json`, `frontend/vite.config.ts`, and `frontend/index.html` for a React TypeScript Vite app.
- [x] 1.3 Configure the Vite dev-server proxy so browser calls to `/api` reach the local backend at `http://127.0.0.1:8000`.
- [x] 1.4 Add `frontend/src/main.tsx`, `frontend/src/App.tsx`, and global styles for the Router task workspace shell.
- [x] 1.5 Add a frontend type strategy that reuses or mirrors `schema/ts/router_contract.d.ts` without ad hoc component-local schema copies.

## 2. Router API Client Layer

- [x] 2.1 Implement shared fetch/error handling in `frontend/src/api/router/client.ts`.
- [x] 2.2 Implement task lifecycle helpers in `frontend/src/api/router/tasks.ts` for create, get, append message, and cancel.
- [x] 2.3 Implement SSE connection and resume helpers in `frontend/src/api/router/events.ts` using `after_seq` cursor support.
- [x] 2.4 Implement artifact list/content helpers in `frontend/src/api/router/artifacts.ts`.
- [x] 2.5 Implement trace summary helper in `frontend/src/api/router/trace.ts`.
- [x] 2.6 Add health-check handling for backend connection status.

## 3. Frontend State And Hooks

- [x] 3.1 Implement `useTaskState` for current `TaskState`, refresh triggers, terminal state detection, and mutation availability.
- [x] 3.2 Implement `useTaskEvents` for EventSource lifecycle, event de-duplication, latest sequence tracking, reconnect state, and terminal stream close behavior.
- [x] 3.3 Implement `useTaskArtifacts` for artifact metadata refresh, selected artifact content loading, content cache, and unsupported preview errors.
- [x] 3.4 Add derived selectors for worker cards, gate readiness, repair rounds, final report artifact discovery, and trace/debug links.
- [x] 3.5 Ensure key events trigger targeted refreshes of task state, artifacts, final report content, and trace summary.

## 4. Task Workspace UI

- [x] 4.1 Implement `TaskWorkspace` as the primary screen with header, chat/input area, execution area, artifact panel, and debug trace drawer.
- [x] 4.2 Implement `ChatPanel` for new task input, project context controls, user follow-up messages, and open clarification questions.
- [x] 4.3 Implement `ExecutionTimeline` for ordered Router events with status, source, severity, timestamps, and correlation links.
- [x] 4.4 Implement `AgentCards` for `plc-dev`, `plc-test`, `plc-formal`, and `plc-repair` worker status and produced artifacts.
- [x] 4.5 Implement gate and repair summary UI in the execution workspace using `TaskState.gates`, trace gate results, and repair counters.
- [x] 4.6 Implement `ArtifactPanel` with artifact metadata list, type/status badges, lazy content loading, JSON/text/code preview, and unsupported content states.
- [x] 4.7 Implement `FinalReportView` for Router final report JSON sections including delivery artifacts, validation, repairs, assumptions, unresolved items, and trace refs.
- [x] 4.8 Implement `TraceView` for main-agent runs, worker jobs, artifacts, gate results, event summaries, and cross-entity navigation.
- [x] 4.9 Add visible backend health, API error, mutation conflict, and SSE connection states.
- [x] 4.10 Add responsive layout behavior for narrow and desktop viewport sizes.

## 5. Local Development Launcher

- [x] 5.1 Add root `main.py` as the `uv run main.py` local launcher entrypoint.
- [x] 5.2 Load `.env` values when present and derive backend, frontend, database, artifact root, and worker settings.
- [x] 5.3 Ensure the configured artifact root exists before backend startup.
- [x] 5.4 Add PostgreSQL handling that can start the existing Docker Compose `postgres` service or verify an externally managed `DATABASE_URL`.
- [x] 5.5 Run Alembic migrations by default after database readiness unless disabled by a launcher flag.
- [x] 5.6 Start the backend API process with prefixed logs and readiness checks against `/api/health`.
- [x] 5.7 Start the frontend dev server process with prefixed logs and readiness checks against the configured frontend URL.
- [x] 5.8 Start the local PLC worker MCP server when effective worker configuration or launcher flags require it.
- [x] 5.9 Print a startup process table with names, PIDs, ports or service identifiers, status, and command.
- [x] 5.10 Print access URLs for frontend, backend base, health, OpenAPI docs, task API base, task SSE pattern, and MCP worker when running.
- [x] 5.11 Handle Ctrl-C and unexpected child exits by terminating launcher-managed processes and printing a shutdown summary.

## 6. Documentation And Verification

- [x] 6.1 Update `docs/local-dev.md` with the `uv run main.py` launcher path, prerequisites, flags, process output, URLs, and shutdown behavior.
- [x] 6.2 Preserve manual startup documentation for PostgreSQL, migrations, backend, frontend, and worker server paths.
- [x] 6.3 Add or update focused frontend smoke tests or scripts for task creation, SSE resume, artifact rendering, final report rendering, and error states.
- [x] 6.4 Add or update launcher tests for process command construction, readiness failure handling, URL summary output, and shutdown behavior.
- [x] 6.5 Run backend validation commands relevant to the touched Python code.
- [x] 6.6 Run frontend typecheck/build validation commands.
- [x] 6.7 Run a local smoke check demonstrating the launcher starts the stack and prints the expected access endpoints.
