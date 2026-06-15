## 1. Dependencies and Database Infrastructure

- [x] 1.1 Add SQLAlchemy 2.x, Alembic, and psycopg dependencies to `pyproject.toml` and refresh `uv.lock`.
- [x] 1.2 Add a database module that creates a synchronous SQLAlchemy engine from `Settings.database_url`.
- [x] 1.3 Add a session factory and a small session dependency/helper suitable for repository tests and later FastAPI wiring.
- [x] 1.4 Ensure base health endpoints still do not open database connections during startup or request handling.

## 2. ORM Models and Migrations

- [x] 2.1 Define a shared SQLAlchemy declarative `Base` for backend database models.
- [x] 2.2 Implement ORM rows for `tasks`, `artifacts`, `events`, `worker_jobs`, and `gate_results` in `backend/app/models/db_models.py`.
- [x] 2.3 Add primary keys, foreign keys, timestamps, JSON payload columns, and projection columns described by the persistence spec.
- [x] 2.4 Add indexes for task lookup, artifact lookup, event ordering, worker job lookup, and gate result listing.
- [x] 2.5 Add a unique constraint on `(task_id, seq)` for events.
- [x] 2.6 Configure Alembic to load application settings and ORM metadata.
- [x] 2.7 Create the initial Alembic revision that upgrades and downgrades the five persistence tables.

## 3. Repository Implementation

- [x] 3.1 Implement `TaskRepository.create_task`, `get_task`, and task state update behavior using validated `TaskState` payloads.
- [x] 3.2 Implement `EventRepository.append_event` with transactional per-task sequence allocation and ordered event listing.
- [x] 3.3 Implement `ArtifactRepository.create_artifact` and `get_artifact` with insert-only duplicate artifact ID handling.
- [x] 3.4 Implement `WorkerJobRepository.create_job`, `get_job`, and completion/update behavior for persisted `WorkerInput` and `WorkerResult` payloads.
- [x] 3.5 Implement `GateResultRepository.create_result` and task-scoped listing for internal gate result records.
- [x] 3.6 Add focused repository error types or reuse existing core errors for not-found and conflict cases.

## 4. Test Coverage

- [x] 4.1 Add repository test fixtures for creating isolated database sessions and schema setup.
- [x] 4.2 Test that `create_task` followed by `get_task` restores a complete `TaskState`.
- [x] 4.3 Test that task projection columns match the saved `TaskState`.
- [x] 4.4 Test that `append_event` assigns `seq` values `1`, then `2`, for the same task.
- [x] 4.5 Test that duplicate `(task_id, seq)` event rows are rejected by the database constraint.
- [x] 4.6 Test that `create_artifact` followed by `get_artifact` restores a complete `Artifact`.
- [x] 4.7 Test that duplicate artifact IDs are rejected instead of overwritten.
- [x] 4.8 Test that a worker job can be created from `WorkerInput` and completed with `WorkerResult`.
- [x] 4.9 Test that gate results can be created and listed by task.
- [x] 4.10 Test that `GET /health` and `GET /api/health` still return the base liveness payload when the database URL points to an unavailable database.

## 5. Development Seed and Verification

- [x] 5.1 Add `scripts/dev_seed_task.py` to insert a representative task, event, artifact, worker job, and gate result through repositories.
- [x] 5.2 Verify `uv run python -m compileall backend` passes.
- [x] 5.3 Verify `uv run pytest backend/app/tests/unit -q` passes.
- [x] 5.4 Verify `uv run alembic upgrade head` creates the persistence tables against the configured database.
- [x] 5.5 Verify `uv run python scripts/dev_seed_task.py` creates records visible through manual SQL queries.
- [x] 5.6 Verify `git diff --check` passes.
