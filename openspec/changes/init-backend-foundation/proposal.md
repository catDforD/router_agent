## Why

The backend plan calls for a minimal FastAPI service that can start, load configuration, emit logs, and answer health checks before database, agent, artifact, or worker behavior is added. The repository currently has the intended backend module layout, but the FastAPI entrypoint, configuration, logging, and health API files are still placeholders.

This change creates a stable foundation for later schema, repository, task API, event, artifact, and MCP work without coupling the first runnable service to external dependencies such as PostgreSQL or OpenAI.

## What Changes

- Add the minimal FastAPI runtime dependencies needed for local backend startup.
- Implement the backend application entrypoint with a reusable app factory and router registration.
- Add environment-driven settings for app name, environment, database URL, artifact root, MCP mode, OpenAI API key, and log level.
- Add standard library logging configuration that can be initialized during app startup without exposing secrets.
- Add `GET /health` and `GET /api/health` endpoints that return the documented status payload.
- Keep health checks independent of database connectivity; dependency checks remain out of scope for this change.
- Add focused verification for configuration defaults and both health endpoints.

## Capabilities

### New Capabilities

- `backend-health-foundation`: Covers backend startup, configuration loading, logging initialization, and database-independent health check behavior.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `pyproject.toml`
  - `uv.lock`
  - `backend/app/main.py`
  - `backend/app/core/config.py`
  - `backend/app/core/logging.py`
  - `backend/app/api/health.py`
  - focused tests under the backend test tree
- New dependencies:
  - `fastapi`
  - `uvicorn`
  - `pydantic-settings`
- Public API impact:
  - Adds `GET /health`
  - Adds `GET /api/health`
- Explicitly out of scope:
  - Database connection checks
  - SQLAlchemy or SQLModel setup
  - Task API, Event SSE, Artifact Store, MCP adapter, Main Agent, and OpenAI Agents SDK integration
