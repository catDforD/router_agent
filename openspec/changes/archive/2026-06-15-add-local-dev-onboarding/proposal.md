## Why

The backend now depends on PostgreSQL migrations and local artifact storage for realistic development checks, but a new developer has no documented, repeatable path from a fresh clone to a working local runtime. This change makes the local setup explicit and verifiable.

## What Changes

- Add a committed `.env.example` with local-safe defaults for app, database, artifact root, MCP mode, and logging.
- Add a Docker Compose PostgreSQL service that matches the project's default `DATABASE_URL`.
- Add local development documentation covering Docker setup, WSL/PostgreSQL fallback setup, migrations, artifact creation, API verification, reset commands, and common troubleshooting.
- Add a small setup helper script that prepares the artifact directory, installs Python dependencies, runs migrations, and creates representative local artifacts.
- Keep local runtime data and secrets out of Git.

## Capabilities

### New Capabilities

- `local-dev-onboarding`: Provides documented and scripted local setup for PostgreSQL-backed backend development and artifact store verification.

### Modified Capabilities

- None.

## Impact

- Affected files:
  - `.env.example`
  - `docker-compose.yml`
  - `docs/local-dev.md`
  - `scripts/dev_setup_db.sh`
  - `.gitignore` if needed for local runtime data
- No application runtime behavior, Router v1 schemas, migrations, or API contracts are changed.
