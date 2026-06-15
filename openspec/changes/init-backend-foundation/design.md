## Context

`docs/backend.md` defines the first backend milestone as a minimal FastAPI service that can start, answer health checks, load configuration, and initialize logging. The repository already contains the intended backend package structure and a complete router schema model, but most runtime modules are placeholders.

This design treats the change as a narrow foundation layer. It prepares the service shell needed by later schema, database, task, artifact, event, MCP, and Main Agent work while avoiding early coupling to systems that are not part of the first milestone.

## Goals / Non-Goals

**Goals:**

- Provide a runnable FastAPI app at `app.main:app` when commands are run from `backend/`.
- Load documented environment variables with local-safe defaults.
- Initialize a shared logging configuration during application startup.
- Expose `GET /health` and `GET /api/health` with the documented response shape.
- Keep the base health check available even when PostgreSQL or other dependencies are unavailable.
- Add focused tests and verification commands for the foundation layer.

**Non-Goals:**

- Connecting to PostgreSQL or checking database readiness.
- Adding SQLAlchemy, SQLModel, Alembic, Redis, or OpenAI Agents SDK.
- Implementing `GET /api/health/dependencies`.
- Implementing Task API, Event SSE, Artifact Store, MCP adapter, Main Agent, or runtime loop behavior.
- Changing the existing router schema contract.

## Decisions

1. Run the backend from the `backend/` directory using `uv run uvicorn app.main:app --reload`.

   This matches the import style already used in the backend plan, where modules are addressed as `app.*`. The alternative is running from the repository root with `backend.app.main:app`, but that would make local commands diverge from the documented backend structure and later `python -m app...` commands.

2. Use `FastAPI` with an app factory plus module-level `app`.

   `create_app()` keeps tests simple and gives future integration work a clean hook for dependency injection. The module-level `app` preserves the standard Uvicorn entrypoint. The alternative is constructing the app entirely at import time without a factory, which is slightly shorter but less flexible for tests.

3. Use `pydantic-settings` for environment configuration.

   The project already depends on Pydantic v2, and settings are a natural extension of that stack. It provides typed defaults for `APP_ENV`, `DATABASE_URL`, `ARTIFACT_ROOT`, `OPENAI_API_KEY`, `MCP_MODE`, and `LOG_LEVEL`. The alternative is reading `os.environ` directly, but that would scatter parsing and default handling across modules.

4. Keep `/health` and `/api/health` identical and dependency-independent.

   The documented expectation is that disconnecting the database must not break basic health. Database and external dependency checks should be added later under `/api/health/dependencies`, so orchestrators and developers have a cheap liveness signal before persistence exists.

5. Use standard library logging for the foundation layer.

   `logging.config.dictConfig` is sufficient for the first milestone and avoids adding a dedicated logging dependency. The format can include timestamp, level, logger, and message while avoiding environment secret values. Structured logging can be revisited when trace mapping and worker jobs are implemented.

## Risks / Trade-offs

- Health checks may look too successful because they do not check PostgreSQL. → Keep the endpoint explicitly scoped to process liveness and add dependency checks in a later change.
- Running from `backend/` may surprise contributors who run commands from the repository root. → Document the exact acceptance command in tasks and keep package imports consistent.
- Adding `uvicorn` as a runtime dependency may be more than libraries need. → This repository is intended to run a local backend service, and the acceptance command requires Uvicorn.
- Logging configured at startup can interfere with test log capture if too aggressive. → Keep logging setup idempotent and avoid destructive changes to existing third-party loggers.

## Migration Plan

1. Add runtime dependencies through `uv` so `pyproject.toml` and `uv.lock` stay synchronized.
2. Implement configuration, logging, app creation, and health endpoints.
3. Add focused tests for settings defaults and health responses.
4. Verify with compile checks, tests, whitespace checks, and the documented local Uvicorn commands.

Rollback is straightforward: remove the added dependencies and revert the foundation module changes. No data migration or external service changes are involved.

## Open Questions

- Whether to add `.env.example` now or wait for the later deployment-readiness milestone. This change should not require it.
- Whether future dependency health should include only PostgreSQL first, or also artifact root and MCP worker reachability.
