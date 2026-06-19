## Why

The backend now exposes a complete task lifecycle API, SSE event stream, artifact APIs, final report artifacts, and trace projections, while the frontend directory is still only an empty scaffold. Developers also need one local entrypoint that can start the frontend, backend, and related services together and print the process/URL summary needed to use the system.

## What Changes

- Build a Router frontend task workspace that lets a user create a task, follow execution through SSE, answer clarification questions, cancel runnable tasks, inspect worker/gate progress, browse artifacts, render final reports, and open debug trace views.
- Add typed frontend API helpers for the existing Router public endpoints without introducing new backend HTTP APIs.
- Add a frontend state model that treats SSE as an append-only event log and uses `TaskState`, artifact lists, artifact content, and trace summary as complementary projections.
- Add a local development launcher with a command shaped like `uv run main.py` that starts the backend API, frontend dev server, and configured supporting services, then prints the started process table and access URLs.
- Update local development documentation to describe the new launcher and how it relates to existing setup commands.
- No breaking changes to Router v1 schemas or existing backend endpoints.

## Capabilities

### New Capabilities

- `router-frontend-workspace`: Browser-based task workspace for creating Router tasks, streaming execution progress, rendering task state, displaying artifacts/final reports, handling clarification/cancel flows, and exposing trace/debug views.

### Modified Capabilities

- `local-dev-onboarding`: Add a one-command local development launcher that orchestrates frontend, backend, and related services and prints process and endpoint information.

## Impact

- Affected code: `frontend/`, root-level local launcher entrypoint, local development docs, and potentially lightweight backend/frontend integration configuration such as Vite proxy settings.
- Affected APIs: no new public backend APIs; frontend consumes existing `/api/tasks`, `/api/tasks/{task_id}/events`, `/api/tasks/{task_id}/artifacts`, `/api/artifacts/{artifact_id}`, `/api/tasks/{task_id}/trace`, and health endpoints.
- Dependencies: frontend package dependencies for React/Vite/TypeScript UI runtime and dev tooling; local launcher may use existing Python/uv tooling and subprocess management.
- Systems: local PostgreSQL/artifact store setup remains governed by existing local development setup, while the launcher verifies or starts configured runtime processes and reports their URLs.
