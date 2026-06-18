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

### Requirement: Router v1 supports Main Agent observability event types
The Router v1 schema contract SHALL include event types needed to represent Main Agent orchestration turn progress, tool calls, tool results, and completion.

#### Scenario: Main Agent turn event type validates
- **WHEN** a Router event payload uses type `main_agent.turn_started`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the event

#### Scenario: Main Agent tool event types validate
- **WHEN** Router event payloads use types `main_agent.tool_called` and `main_agent.tool_result`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the events

#### Scenario: Main Agent completed event type validates
- **WHEN** a Router event payload uses type `main_agent.completed`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the event

### Requirement: Main Agent completed events reference report artifacts
The Router v1 schema contract SHALL allow `main_agent.completed` events to reference final report and replay log artifacts through existing event correlation and payload fields.

#### Scenario: Completed event carries artifact references
- **WHEN** a `main_agent.completed` event is created after a successful episode
- **THEN** the event correlation includes the final report artifact ID and replay log artifact ID when available
- **AND** the payload includes `final_report_artifact_id`, `main_agent_log_artifact_id`, `final_task_status`, and compact summary fields

### Requirement: Final report and Main Agent log artifact types remain stable
The Router v1 schema contract SHALL use existing artifact type values `final_report` and `main_agent_log` for Main Agent report and replay artifacts.

#### Scenario: Final report artifact validates
- **WHEN** a Router artifact payload uses type `final_report`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the artifact

#### Scenario: Main Agent log artifact validates
- **WHEN** a Router artifact payload uses type `main_agent_log`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the artifact

### Requirement: TypeScript declarations include Main Agent observability values
The TypeScript Router contract declaration SHALL include the Main Agent observability event type values and existing report artifact type values.

#### Scenario: TypeScript event union includes observability events
- **WHEN** a TypeScript consumer imports the Router event type declarations
- **THEN** the event type union includes `main_agent.turn_started`, `main_agent.tool_called`, `main_agent.tool_result`, and `main_agent.completed`

