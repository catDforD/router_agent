## Context

`docs/backend.md` defines the first backend milestone as a minimal FastAPI service that can start, answer health checks, load configuration, and initialize logging. The repository already contains the intended `backend/app` package structure and Router v1 schema contracts, but most runtime modules are placeholders and the project metadata only includes Pydantic.

This design treats the change as a narrow foundation layer. It prepares the service shell needed by later schema, database, task, artifact, event, MCP, and Main Agent work while avoiding early coupling to systems that are not part of the first milestone.

## Goals / Non-Goals

**Goals:**

- Provide a runnable FastAPI app at `app.main:app` when commands are run from the `backend/` directory.
- Load documented environment variables with local-safe defaults.
- Initialize shared logging during application startup.
- Expose `GET /health` and `GET /api/health` with the documented response shape.
- Keep the base health check available when PostgreSQL, OpenAI, MCP workers, or artifact storage are unavailable.
- Add focused tests and verification commands for the foundation layer.

**Non-Goals:**

- Connecting to PostgreSQL or checking database readiness.
- Adding SQLAlchemy, SQLModel, Alembic, Redis, or OpenAI Agents SDK.
- Implementing `GET /api/health/dependencies`.
- Implementing Task API, Event SSE, Artifact Store, MCP adapter, Main Agent, or runtime loop behavior.
- Changing the existing Router v1 Pydantic models, JSON Schema files, or TypeScript contract declarations.

## Decisions

1. Run the backend from the `backend/` directory using `uv run uvicorn app.main:app --reload`.

   This matches the import style already used in the backend plan, where modules are addressed as `app.*`. The alternative is running from the repository root with `backend.app.main:app`, but that would make local commands diverge from the documented backend structure and later `python -m app...` commands.

2. Use FastAPI with an app factory plus a module-level `app`.

   `create_app()` keeps tests simple and gives future integration work a clean hook for dependency injection. The module-level `app` preserves the standard Uvicorn entrypoint. The alternative is constructing the app entirely at import time without a factory, which is slightly shorter but less flexible for tests.

3. Use Pydantic Settings for environment configuration.

   The project already depends on Pydantic v2, and settings are a natural extension of that stack. It provides typed defaults for `APP_ENV`, `DATABASE_URL`, `ARTIFACT_ROOT`, `OPENAI_API_KEY`, `MCP_MODE`, and `LOG_LEVEL`. The alternative is reading `os.environ` directly, but that would scatter parsing and default handling across modules.

4. Keep `/health` and `/api/health` identical and dependency-independent.

   The documented expectation is that disconnecting the database must not break basic health. Database and external dependency checks should be added later under `/api/health/dependencies`, so orchestrators and developers have a cheap liveness signal before persistence exists.

5. Use standard library logging for the foundation layer.

   `logging.config.dictConfig` is sufficient for the first milestone and avoids adding a dedicated logging dependency. The format can include timestamp, level, logger, and message while avoiding environment secret values. Structured logging can be revisited when trace mapping and worker jobs are implemented.

6. Keep tests at the foundation boundary.

   Unit and API tests should cover settings defaults, environment overrides, logging behavior, and health endpoints. They should not require a database, OpenAI API key, artifact directory setup, or worker MCP server.

## Risks / Trade-offs

- Base health checks may look too successful because they do not check PostgreSQL. -> Keep the endpoint explicitly scoped to process liveness and add dependency checks in a later change.
- Running from `backend/` may surprise contributors who run commands from the repository root. -> Document the exact acceptance command and keep backend imports consistently under `app.*`.
- Adding Uvicorn as a runtime dependency may be more than a library package needs. -> This repository is intended to run a local backend service, and the documented acceptance command requires Uvicorn.
- Logging configured during app startup can interfere with test log capture if too aggressive. -> Keep logging setup idempotent and avoid destructive changes to existing third-party loggers.
- Settings include sensitive values such as `OPENAI_API_KEY` and possibly database credentials. -> Do not log raw settings or secret-bearing connection strings.

## Migration Plan

1. Add dependencies through `uv` so `pyproject.toml` and `uv.lock` stay synchronized.
2. Implement configuration, logging, app creation, and health endpoints.
3. Add focused tests for settings defaults, environment overrides, logging initialization, and health responses.
4. Verify with compile checks, tests, whitespace checks, and the documented local Uvicorn command.

Rollback is straightforward: remove the added dependencies and revert the foundation module and test changes. No data migration or external service migration is involved.

## Open Questions

- Whether to add `.env.example` now or wait for a later deployment-readiness milestone. This change does not require it.
- Whether future dependency health should include only PostgreSQL first, or also artifact root availability and MCP worker reachability.
