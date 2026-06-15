## ADDED Requirements

### Requirement: Backend app starts through the documented entrypoint
The backend SHALL expose a FastAPI application as `app.main:app` when run from the `backend/` directory.

#### Scenario: Uvicorn starts the backend app
- **WHEN** a developer runs `cd backend` followed by `uv run uvicorn app.main:app --reload`
- **THEN** Uvicorn starts the Router backend application without requiring database connectivity

### Requirement: Runtime settings load from environment variables
The backend SHALL load typed runtime settings from environment variables with local-safe defaults for app name, app environment, database URL, artifact root, MCP mode, OpenAI API key, and log level.

#### Scenario: Defaults support local startup
- **WHEN** the backend starts without a local `.env` file
- **THEN** settings use `router-backend` as the app name, `local` as the environment, `mock` as the MCP mode, and the documented local database URL as a value without opening a database connection

#### Scenario: Environment variables override defaults
- **WHEN** supported environment variables such as `APP_ENV`, `DATABASE_URL`, `ARTIFACT_ROOT`, `MCP_MODE`, or `LOG_LEVEL` are set before startup
- **THEN** the backend uses those values in its settings object

### Requirement: Logging initializes for the backend process
The backend SHALL initialize a shared logging configuration during application startup.

#### Scenario: Startup emits non-secret operational context
- **WHEN** the backend application starts
- **THEN** logs identify the application and environment without logging secret values such as `OPENAI_API_KEY`

### Requirement: Base health endpoints report service liveness
The backend SHALL expose `GET /health` and `GET /api/health` endpoints that return the documented base health payload.

#### Scenario: Root health endpoint succeeds
- **WHEN** a client requests `GET /health`
- **THEN** the response status is 200 and the body contains `status: "ok"`, `app: "router-backend"`, and the active environment value

#### Scenario: API health endpoint succeeds
- **WHEN** a client requests `GET /api/health`
- **THEN** the response status is 200 and the body contains the same base health payload as `GET /health`

### Requirement: Base health remains independent of external dependencies
The backend SHALL keep the base health endpoints independent from PostgreSQL, OpenAI, MCP workers, and artifact storage availability.

#### Scenario: Database is unavailable
- **WHEN** PostgreSQL is stopped, unreachable, or not configured beyond the `DATABASE_URL` setting
- **THEN** `GET /health` and `GET /api/health` still return the base `ok` health response
