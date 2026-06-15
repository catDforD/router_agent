## ADDED Requirements

### Requirement: Database migrations create runtime persistence tables
The backend SHALL provide Alembic migration support that creates the runtime persistence tables `tasks`, `artifacts`, `events`, `worker_jobs`, and `gate_results` from an empty database.

#### Scenario: Upgrade creates all persistence tables
- **WHEN** a developer runs `uv run alembic upgrade head` against the configured database
- **THEN** the database contains `tasks`, `artifacts`, `events`, `worker_jobs`, and `gate_results`

#### Scenario: Downgrade removes persistence tables
- **WHEN** a developer downgrades the initial persistence migration
- **THEN** the migration removes the tables it created for this capability

### Requirement: Task repository persists complete task state
The backend SHALL persist a complete Router v1 `TaskState` payload and restore it as a validated `TaskState` model.

#### Scenario: Create and read task state
- **WHEN** a valid `TaskState` is saved through the task repository
- **THEN** reading the task by ID returns a `TaskState` with the same task ID, session ID, status, phase, task type, difficulty, gates, artifacts, worker job refs, failures, trace, metadata, and event sequence

#### Scenario: Task projection columns match task state
- **WHEN** a valid `TaskState` is saved through the task repository
- **THEN** the `tasks` row stores projection columns for ID, session ID, user ID, status, phase, task type, difficulty level, created time, updated time, and completed time that match the saved payload

### Requirement: Event repository assigns monotonic sequence numbers per task
The backend SHALL append Router events with sequence numbers that increase monotonically within each task.

#### Scenario: Append first event
- **WHEN** a task has `event_seq` equal to `0` and an event is appended for that task
- **THEN** the persisted event has `seq` equal to `1` and the task row records `event_seq` equal to `1`

#### Scenario: Append subsequent event
- **WHEN** a task has an existing persisted event with `seq` equal to `1` and another event is appended for that task
- **THEN** the new persisted event has `seq` equal to `2` and event listing for that task returns events ordered by sequence

#### Scenario: Duplicate event sequence is rejected
- **WHEN** two event rows for the same task would use the same `seq`
- **THEN** the database constraint rejects the duplicate sequence

### Requirement: Artifact repository stores immutable artifact metadata
The backend SHALL persist Router v1 `Artifact` metadata without overwriting existing artifact rows.

#### Scenario: Create and read artifact
- **WHEN** a valid `Artifact` is saved through the artifact repository
- **THEN** reading the artifact by ID returns a validated `Artifact` with the same storage URI, content hash, summary, visibility, metadata, parent references, and inline content

#### Scenario: Duplicate artifact ID is rejected
- **WHEN** `create_artifact` is called with an artifact ID that already exists
- **THEN** the repository rejects the write instead of updating the existing artifact row

#### Scenario: Artifact query projections are available
- **WHEN** an artifact is persisted
- **THEN** the `artifacts` row stores projection columns for task ID, artifact type, version, status, visibility, URI, content hash, summary, created time, and updated time

### Requirement: Worker job repository persists worker input and result
The backend SHALL persist worker job lifecycle state, including the original `WorkerInput` and terminal `WorkerResult` payload when available.

#### Scenario: Create running worker job
- **WHEN** a valid `WorkerInput` is used to create a running worker job
- **THEN** the worker job row stores the task ID, worker type, status, started time, and full input payload

#### Scenario: Complete worker job with result
- **WHEN** a running worker job is completed with a valid `WorkerResult`
- **THEN** the worker job row stores terminal status, completed time, and the full result payload

#### Scenario: Read worker job restores payloads
- **WHEN** a worker job with input and result payloads is read from the repository
- **THEN** the repository returns validated Router v1 payloads for the worker input and worker result

### Requirement: Gate result repository persists quality gate outcomes
The backend SHALL persist quality gate result records with task linkage, gate type, status, blocking flag, evidence artifact IDs, result payload, and creation time.

#### Scenario: Create gate result
- **WHEN** a gate result is saved for a task
- **THEN** the gate result row stores the task ID, gate type, status, blocking flag, evidence artifact IDs, result payload, and creation time

#### Scenario: List gate results for task
- **WHEN** multiple gate results exist for a task
- **THEN** listing gate results for that task returns the persisted records ordered by creation time

### Requirement: Base health endpoints remain independent of database availability
The backend SHALL keep base health endpoints independent from database connectivity even after database dependencies are introduced.

#### Scenario: Health succeeds when database is unavailable
- **WHEN** the configured database is stopped, unreachable, or missing
- **THEN** `GET /health` and `GET /api/health` still return the base liveness payload with HTTP 200

### Requirement: Development seed script creates inspectable runtime records
The repository SHALL provide a development seed script that writes representative runtime records for manual database inspection.

#### Scenario: Seed script creates task and event rows
- **WHEN** a developer runs `uv run python scripts/dev_seed_task.py` against a migrated database
- **THEN** querying `tasks` returns a seeded task and querying `events` ordered by sequence returns seeded events for that task

#### Scenario: Seed script creates artifact and worker job rows
- **WHEN** a developer runs `uv run python scripts/dev_seed_task.py` against a migrated database
- **THEN** querying `artifacts` and `worker_jobs` returns records linked to the seeded task
