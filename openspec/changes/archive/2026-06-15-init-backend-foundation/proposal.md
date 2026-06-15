## Why

The backend plan calls for a minimal FastAPI service that can start, load configuration, emit logs, and answer health checks before database, agent, artifact, or worker behavior is added. The repository already has the intended backend module layout and Router v1 schema contracts, but the FastAPI entrypoint, configuration, logging, and health API modules are still placeholders.

This change creates a stable foundation for later schema, repository, task API, event, artifact, MCP, and Main Agent work without coupling the first runnable service to external dependencies such as PostgreSQL, OpenAI, or worker MCP servers.

## What Changes

- Add the minimal runtime dependencies needed for local backend startup: FastAPI, Uvicorn, and Pydantic Settings.
- Add focused test dependencies for the foundation layer: pytest and HTTPX.
- Implement the backend application entrypoint with a reusable app factory and router registration.
- Add environment-driven settings for app name, environment, database URL, artifact root, MCP mode, OpenAI API key, and log level.
- Add standard library logging configuration that can be initialized during app startup without exposing secret values.
- Add `GET /health` and `GET /api/health` endpoints that return the documented base health payload.
- Keep base health checks independent of database, OpenAI, MCP worker, and artifact storage connectivity.
- Add focused verification for configuration defaults, environment overrides, logging initialization, and both health endpoints.

## Capabilities

### New Capabilities

- `backend-health-foundation`: Covers backend startup, configuration loading, logging initialization, and dependency-independent base health check behavior.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `pyproject.toml`
  - `uv.lock`
  - `backend/app/main.py`
  - `backend/app/api/health.py`
  - `backend/app/core/config.py`
  - `backend/app/core/logging.py`
  - focused tests under the backend test tree
- New runtime dependencies:
  - `fastapi`
  - `uvicorn`
  - `pydantic-settings`
- New development/test dependencies:
  - `pytest`
  - `httpx`
- Public API impact:
  - Adds `GET /health`
  - Adds `GET /api/health`
- Explicitly out of scope:
  - Database connection checks and `GET /api/health/dependencies`
  - SQLAlchemy, SQLModel, Alembic, Redis, or OpenAI Agents SDK setup
  - Task API, Event SSE, Artifact Store, MCP adapter, Main Agent, and runtime loop behavior
  - Changes to Router v1 Pydantic models, JSON Schema files, or TypeScript contract declarations
