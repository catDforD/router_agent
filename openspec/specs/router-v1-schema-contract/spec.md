# router-v1-schema-contract Specification

## Purpose
TBD - created by archiving change complete-router-v1-schema-contract. Update Purpose after archive.
## Requirements
### Requirement: Router v1 Pydantic schemas validate the five core contract objects
The backend SHALL provide strict Pydantic validation models for Router v1 `TaskState`, `WorkerInput`, `WorkerResult`, `Artifact`, and `RouterEvent`.

#### Scenario: Valid task state is accepted
- **WHEN** a valid Router v1 task state payload is parsed through the backend `TaskState` model
- **THEN** validation succeeds and preserves the task identity, status, phase, artifact references, runtime limits, gates, assumptions, questions, failures, trace, and event sequence fields

#### Scenario: Invalid schema version is rejected
- **WHEN** any top-level Router v1 contract payload uses a schema version other than `router.v1`
- **THEN** backend validation fails

#### Scenario: Missing worker input task id is rejected
- **WHEN** a Router v1 `WorkerInput` payload omits `task_id`
- **THEN** backend validation fails

#### Scenario: Router event sequence must be an integer
- **WHEN** a Router v1 `RouterEvent` payload provides a non-integer `seq`
- **THEN** backend validation fails

### Requirement: Worker result separates execution status from business outcome
The backend SHALL validate `WorkerResult.execution_status` as the worker call status separately from `WorkerResult.outcome.status` as the domain result.

#### Scenario: Completed worker call can report failed outcome
- **WHEN** a Router v1 worker result payload has `execution_status: "completed"` and `outcome.status: "failed"`
- **THEN** backend validation succeeds and preserves both values distinctly

### Requirement: Artifact payloads support externalized large content
The backend SHALL allow Router v1 `Artifact` payloads to omit `inline_content` when artifact content is stored through `storage.uri`.

#### Scenario: Artifact without inline content is accepted
- **WHEN** a Router v1 artifact payload includes valid storage metadata and does not include `inline_content`
- **THEN** backend validation succeeds

### Requirement: JSON Schema export is repeatable for the five core schemas
The backend SHALL provide a repeatable JSON Schema export command for the five Router v1 core schemas.

#### Scenario: Export command writes documented schema files
- **WHEN** a developer runs `python -m app.schemas.json_schema_export` from the `backend/` directory
- **THEN** JSON Schema files are written for `task_state`, `worker_input`, `worker_result`, `artifact`, and `router_event`

#### Scenario: Exported schemas include stable contract metadata
- **WHEN** JSON Schema files are exported
- **THEN** each exported schema includes JSON Schema dialect metadata, a stable schema identifier, and `x-schema-version: "router.v1"`

### Requirement: Valid fixtures remain parseable by backend schemas
The repository SHALL include representative valid JSON fixtures for the five Router v1 core schemas and validate them in tests.

#### Scenario: Task state fixture parses
- **WHEN** the `task_state.valid.json` fixture is loaded
- **THEN** the backend `TaskState` model parses it successfully

#### Scenario: PLC dev worker input fixture parses
- **WHEN** the `worker_input.plc_dev.valid.json` fixture is loaded
- **THEN** the backend `WorkerInput` model parses it successfully

#### Scenario: Failed test worker result fixture parses
- **WHEN** the `worker_result.test_failed.valid.json` fixture is loaded
- **THEN** the backend `WorkerResult` model parses it successfully

#### Scenario: PLC code artifact fixture parses
- **WHEN** the `artifact.plc_code.valid.json` fixture is loaded
- **THEN** the backend `Artifact` model parses it successfully

#### Scenario: Worker started event fixture parses
- **WHEN** the `event.worker_started.valid.json` fixture is loaded
- **THEN** the backend `RouterEvent` model parses it successfully

### Requirement: Contract verification fits the existing backend test layout
The schema contract tests SHALL live under the existing backend test tree and run with the repository's configured `uv` and `pytest` workflow.

#### Scenario: Router schema unit tests pass
- **WHEN** a developer runs `uv run pytest backend/app/tests/unit/test_router_schema.py -q`
- **THEN** the tests for documented Router v1 schema validation behavior pass

#### Scenario: Schema fixture tests pass
- **WHEN** a developer runs `uv run pytest backend/app/tests/unit/test_schema_fixtures.py -q`
- **THEN** the tests proving all committed valid schema fixtures parse successfully pass

