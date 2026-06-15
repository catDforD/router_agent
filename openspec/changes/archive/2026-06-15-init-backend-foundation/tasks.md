## 1. Dependencies and Package Shape

- [x] 1.1 Add FastAPI, Uvicorn, and Pydantic Settings as runtime dependencies through `uv`.
- [x] 1.2 Add pytest and HTTPX as development/test dependencies through `uv`.
- [x] 1.3 Ensure `backend/app` and relevant subpackages are importable when running from the `backend/` directory.

## 2. Configuration

- [x] 2.1 Implement a typed settings model in `backend/app/core/config.py` with defaults for app name, app environment, database URL, artifact root, MCP mode, OpenAI API key, and log level.
- [x] 2.2 Support environment variable overrides for the documented settings without requiring a local `.env` file.
- [x] 2.3 Avoid opening database, OpenAI, artifact store, or MCP connections while loading settings.

## 3. Logging

- [x] 3.1 Implement idempotent standard library logging setup in `backend/app/core/logging.py`.
- [x] 3.2 Log startup context with application name and environment while avoiding secret values and password-bearing connection strings.

## 4. FastAPI App and Health API

- [x] 4.1 Implement `create_app()` and module-level `app` in `backend/app/main.py`.
- [x] 4.2 Register the health router during application creation.
- [x] 4.3 Implement `GET /health` in `backend/app/api/health.py` with `status`, `app`, and `env`.
- [x] 4.4 Implement `GET /api/health` with the same base health payload as `GET /health`.
- [x] 4.5 Keep both base health endpoints independent from database, OpenAI, artifact store, and MCP worker availability.

## 5. Tests

- [x] 5.1 Add focused tests for settings defaults.
- [x] 5.2 Add focused tests for environment variable overrides.
- [x] 5.3 Add focused tests for both health endpoints.
- [x] 5.4 Add a test or assertion that invalid/unavailable external dependency configuration does not break base health responses.
- [x] 5.5 Add logging coverage that verifies startup logs do not expose `OPENAI_API_KEY`.

## 6. Verification

- [x] 6.1 Run `uv run python -m compileall backend`.
- [x] 6.2 Run the focused backend test suite for this change.
- [x] 6.3 Run `git diff --check`.
- [x] 6.4 Verify the documented command from `backend/`: `uv run uvicorn app.main:app --reload`.
