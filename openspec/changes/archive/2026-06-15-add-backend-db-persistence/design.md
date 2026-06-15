## Context

The backend currently has local-safe configuration, health endpoints, and strict Router v1 Pydantic models for `TaskState`, `WorkerInput`, `WorkerResult`, `Artifact`, and `RouterEvent`. The planned repository files and `db_models.py` exist as placeholders, and the project has no SQLAlchemy, Alembic, or PostgreSQL driver dependency yet.

The database layer is the next runtime boundary. It must support task state recovery, event replay, artifact metadata lookup, worker job lifecycle tracking, and quality gate history without changing the Router v1 contract.

## Goals / Non-Goals

**Goals:**

- Introduce database dependencies and Alembic migration infrastructure.
- Define ORM models for `tasks`, `artifacts`, `events`, `worker_jobs`, and `gate_results`.
- Persist complete Router v1 payloads as JSON while maintaining indexed projection columns for common queries.
- Provide repository methods that validate Pydantic objects on write and restore Pydantic objects on read.
- Guarantee per-task event sequence monotonicity and artifact insert-only behavior.
- Add focused repository tests and a development seed script that prove the documented persistence behavior.

**Non-Goals:**

- Implement public task, event, artifact, or worker APIs.
- Implement the local Artifact Store content layer.
- Implement real worker scheduling, MCP calls, or Runtime orchestration.
- Change Router v1 schema strings, enum values, JSON Schema files, or TypeScript declarations.
- Require database connectivity for `GET /health` or `GET /api/health`.

## Decisions

### Use SQLAlchemy ORM and Alembic

Use SQLAlchemy 2.x declarative ORM models plus Alembic migrations. SQLModel is not needed because the Pydantic v2 Router contract already exists and should remain separate from persistence concerns.

Alternative considered: SQLModel would reduce some boilerplate, but it risks blurring external contract models with internal row models. Keeping them separate makes migrations and contract evolution easier to reason about.

### Store full contract payloads plus query projections

Each runtime object stores its complete validated payload in a JSON column:

- `tasks.state_json`
- `artifacts.artifact_json`
- `events.event_json`
- `worker_jobs.input_json`
- `worker_jobs.result_json`
- `gate_results.result_json`

Columns such as `status`, `phase`, `task_type`, `type`, `seq`, `worker_type`, `uri`, and `content_hash` are projections derived from the payload or from repository inputs.

Alternative considered: fully normalizing every nested field from `TaskState` and `Artifact`. That would create early migration churn because many nested Router fields are still runtime-facing contract data rather than relational query dimensions.

### Keep repositories as the validation boundary

Repositories should accept and return Pydantic models where a Router v1 contract model exists. On create/update, repositories derive projection columns from the Pydantic object instead of accepting duplicate caller-provided values. On read, repositories validate the stored JSON back into the Pydantic model.

This keeps one source of semantic truth: the Router v1 Pydantic schemas.

### Use synchronous database sessions initially

Use synchronous SQLAlchemy sessions for this first persistence layer. The current backend endpoints are synchronous, and this change focuses on correctness of storage invariants rather than high-concurrency async request handling.

Alternative considered: async SQLAlchemy with asyncpg. That adds driver and test complexity before the backend has request paths that need it.

### Allocate event sequence numbers transactionally

`append_event` should allocate the next per-task sequence number in a transaction by locking the task row, incrementing `tasks.event_seq`, and inserting the event with that assigned `seq`. Add a unique constraint on `(task_id, seq)` as a final guard.

Alternative considered: calculating `max(seq) + 1` from the events table. That is simpler but is race-prone under concurrent event writers.

### Make artifacts insert-only

`create_artifact` should insert a new artifact row and never upsert by artifact ID. A duplicate artifact ID must fail and be surfaced as a repository conflict. Older artifact versions remain addressable by their artifact IDs and stored payloads.

The schema should index `(task_id, type, version)` for lookup, but it should not initially make that tuple unique because the contract only requires duplicate `artifact_id` rejection.

### Treat gate results as internal persistence records

Router v1 has `GateState` in `TaskState`, but no standalone `GateResult` contract. This change should persist `gate_results` as internal records with JSON result payloads and evidence artifact IDs. A future quality-gate change can promote this into an external contract if needed.

## Risks / Trade-offs

- Projection columns can drift from JSON payloads -> Repositories derive projections from validated Pydantic models and tests assert round-trip behavior.
- Event sequence races can produce duplicate seq values -> Use row-level locking around `tasks.event_seq` and a database unique constraint on `(task_id, seq)`.
- JSON-first persistence makes ad hoc analytics harder -> This is acceptable for MVP runtime state; only common query dimensions are projected now.
- SQLite-style repository tests may miss PostgreSQL-specific behavior -> Keep migrations PostgreSQL-oriented and include `alembic upgrade head` as a required verification path against the configured database.
- Gate result shape may evolve -> Keep `result_json` flexible and avoid exposing it as a public contract in this change.

## Migration Plan

1. Add SQLAlchemy, Alembic, and psycopg dependencies.
2. Add database engine/session infrastructure using the existing `DATABASE_URL`.
3. Create the first Alembic revision for `tasks`, `artifacts`, `events`, `worker_jobs`, and `gate_results`.
4. Implement repositories against the ORM models.
5. Add repository tests using isolated test databases or transactions.
6. Add `scripts/dev_seed_task.py` to create a representative task, event, artifact, worker job, and gate result.

Rollback for the first migration is a schema downgrade that drops the five new tables. Because this is the initial persistence layer, no production data migration is expected before the change is applied.

## Open Questions

- Should repository tests run only against PostgreSQL, or should they also support SQLite for fast local unit coverage?
- Should a later change add `GET /api/health/dependencies` to actively check database connectivity?
- Should artifact version uniqueness eventually be enforced per `(task_id, type, version)`, or should only `artifact_id` remain globally unique?
