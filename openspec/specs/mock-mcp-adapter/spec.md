# mock-mcp-adapter Specification

## Purpose
Provides deterministic mock MCP worker execution for Router backend development, including standard WorkerResult outputs, persisted artifacts, worker job records, and replayable worker/artifact events without contacting real MCP servers.

## Requirements
### Requirement: Mock MCP adapter dispatches Router worker inputs
The backend SHALL provide a mock MCP adapter that accepts a valid Router v1 `WorkerInput` for `plc-dev`, `plc-test`, `plc-formal`, or `plc-repair` and returns a validated Router v1 `WorkerResult` without contacting a real MCP server.

#### Scenario: PLC development mock returns a completed result
- **WHEN** the mock adapter is invoked in mock mode with a valid `plc-dev` `WorkerInput`
- **THEN** it returns a `WorkerResult` with matching `task_id`, `worker_job_id`, `worker_type: "plc-dev"`, `mcp_tool: "plc_dev.run"`, and `execution_status: "completed"`

#### Scenario: Unsupported worker type is rejected by validation
- **WHEN** the mock adapter receives input that cannot be validated as a Router v1 `WorkerInput` or whose worker/tool mapping is invalid
- **THEN** it rejects the call before mock worker execution and reports a schema-invalid worker error path

#### Scenario: Real MCP client is not used in mock mode
- **WHEN** `MCP_MODE` is `mock`
- **THEN** the adapter executes the in-process mock worker path and does not require any MCP server URL, network connection, or MCP SDK dependency

### Requirement: Mock worker outputs are persisted as artifacts
The mock MCP adapter SHALL persist mock-produced artifact content through the existing Artifact Store and SHALL include only persisted `ArtifactRef` entries in `WorkerResult.produced_artifacts`.

#### Scenario: PLC development persists requirements and code artifacts
- **WHEN** `plc-dev` completes successfully through the mock adapter
- **THEN** the Artifact Store contains at least `requirements_ir:v1` and `plc_code:v1` artifacts for the task, and the `WorkerResult.produced_artifacts` list references those persisted artifacts

#### Scenario: PLC test pass persists a test report
- **WHEN** `plc-test` completes with a passing mock outcome
- **THEN** the Artifact Store contains a `test_report` artifact and the `WorkerResult` has `outcome.status: "passed"` with test metrics indicating no failed tests

#### Scenario: PLC test failure persists evidence
- **WHEN** `plc-test` completes with a failing mock outcome
- **THEN** the Artifact Store contains `test_report` and `failing_trace` artifacts, and the `WorkerResult` includes a blocking test `Failure` referencing the evidence artifacts

#### Scenario: PLC formal failure persists counterexample evidence
- **WHEN** `plc-formal` completes with a failing mock outcome
- **THEN** the Artifact Store contains `formal_report` and `counterexample` artifacts, and the `WorkerResult` includes a blocking formal `Failure` referencing the evidence artifacts

#### Scenario: PLC repair persists patch and patched code
- **WHEN** `plc-repair` completes successfully through the mock adapter
- **THEN** the Artifact Store contains `patch`, `repair_summary`, and a new-version `plc_code` artifact, and the `WorkerResult.produced_artifacts` references all of them

### Requirement: Mock adapter records worker audit trail
The mock MCP adapter SHALL use existing persistence services to record worker job lifecycle state and append replayable worker and artifact events for each mock invocation.

#### Scenario: Worker job is created and completed
- **WHEN** a mock worker invocation starts and then completes
- **THEN** the `worker_jobs` table contains the original `WorkerInput`, terminal status, completion timestamp, and final `WorkerResult`

#### Scenario: Worker lifecycle events are appended
- **WHEN** a mock worker invocation starts and completes successfully
- **THEN** the task event log contains user-visible `worker.started` and `worker.completed` events correlated with the `worker_job_id`

#### Scenario: Artifact events are appended
- **WHEN** the mock adapter persists produced artifacts
- **THEN** the task event log contains user-visible `artifact.created` events correlated with the created artifact IDs and worker job ID

#### Scenario: Timeout event is appended
- **WHEN** a mock worker invocation is normalized as a timeout
- **THEN** the task event log contains a user-visible `worker.timeout` event correlated with the `worker_job_id`

### Requirement: Mock scenarios are deterministic
The mock worker SHALL support deterministic scenarios selected by configuration or direct adapter input so tests and development scripts can exercise success, failure, repair, clarification, and timeout paths.

#### Scenario: Development and testing pass
- **WHEN** the scenario is `dev_test_pass`
- **THEN** `plc-dev` produces requirements and code artifacts, `plc-test` returns a passed outcome, and `plc-formal` returns a passed outcome when invoked with valid inputs

#### Scenario: Test failure passes after repair
- **WHEN** the scenario is `test_failed_then_repair_pass`
- **THEN** `plc-test` returns a blocking failed outcome for `plc_code:v1`, `plc-repair` produces a patched `plc_code:v2`, and `plc-test` returns a passed outcome for `plc_code:v2`

#### Scenario: Formal failure passes after repair
- **WHEN** the scenario is `formal_failed_then_repair_pass`
- **THEN** `plc-formal` returns a blocking failed outcome for `plc_code:v1`, `plc-repair` produces a patched `plc_code:v2`, and `plc-formal` returns a passed outcome for `plc_code:v2`

#### Scenario: Worker requests clarification
- **WHEN** the scenario is `need_clarification`
- **THEN** the mock worker returns `execution_status: "completed"`, `outcome.status: "need_clarification"`, no code artifact, and a populated `clarification_request`

#### Scenario: Worker timeout is simulated deterministically
- **WHEN** the scenario is `worker_timeout`
- **THEN** the adapter returns a timeout-normalized `WorkerResult` without waiting for a wall-clock timeout

### Requirement: Worker errors are normalized to Router results
The mock MCP adapter SHALL convert timeout, schema-invalid, and mock execution exceptions into standard Router v1 `WorkerResult` payloads with structured `WorkerError` details.

#### Scenario: Timeout normalization returns retryable error
- **WHEN** a mock invocation times out or raises the mock timeout signal
- **THEN** the adapter returns `execution_status: "timeout"`, `outcome.status: "unknown"`, a retryable `WorkerError` with code `MCP_TIMEOUT`, and `next_recommended_action: "retry"`

#### Scenario: Invalid worker output is normalized
- **WHEN** mock or raw worker output cannot be validated as a Router v1 `WorkerResult`
- **THEN** the adapter returns or raises a schema-invalid path with error code `WORKER_SCHEMA_INVALID` and does not complete the worker job as successful

#### Scenario: Execution exception is normalized
- **WHEN** mock worker execution raises an unexpected exception
- **THEN** the adapter records the worker job as error and produces a `WorkerResult` with `execution_status: "error"`, error code `WORKER_EXECUTION_ERROR`, and no produced artifacts
