## Why

Router v1 schemas are defined and validated, but runtime state is still not persistent. The backend needs a durable database boundary before task execution, event replay, artifact metadata, worker job tracking, and quality gates can be wired into real workflows.

## What Changes

- Add PostgreSQL persistence for Router runtime records: tasks, artifacts, events, worker jobs, and gate results.
- Add SQLAlchemy 2.x ORM models and Alembic migration support for creating the initial database schema.
- Add database engine/session infrastructure that uses the existing `DATABASE_URL` setting.
- Add repository methods that persist and restore complete Router v1 Pydantic payloads while exposing indexed query columns for operational access.
- Enforce the core persistence invariants:
  - Task state JSON can be saved and restored without losing contract fields.
  - Event sequence numbers are monotonic per task.
  - Artifact creation is insert-only and rejects duplicate artifact IDs.
  - Worker jobs can transition from running to completed with a persisted result payload.
- Add focused repository tests and a development seed script for manual database inspection.
- Keep base health endpoints independent from database availability.

## Capabilities

### New Capabilities

- `backend-runtime-persistence`: Persists and restores Router runtime task state, artifact metadata, events, worker jobs, and gate results through database migrations and repository APIs.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `pyproject.toml` and `uv.lock` for SQLAlchemy, Alembic, and PostgreSQL driver dependencies.
  - `backend/app/models/db_models.py` for ORM table models.
  - `backend/app/repositories/*.py` for persistence access.
  - A new database/session module under `backend/app/core/` or an equivalent local pattern.
  - New Alembic configuration and migration files.
  - New repository tests under `backend/app/tests/`.
  - A new `scripts/dev_seed_task.py` development helper.
- No Router v1 contract strings, enum values, JSON Schema files, or TypeScript declarations are changed by this proposal.
- No task API, real worker scheduling, or artifact content store behavior is implemented in this change.
