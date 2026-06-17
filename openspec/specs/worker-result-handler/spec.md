# worker-result-handler Specification

## Purpose
TBD - created by archiving change add-worker-result-handler. Update Purpose after archive.
## Requirements
### Requirement: Handler applies worker results to persisted task state
The backend SHALL provide WorkerResult handling that loads the target `TaskState`, validates the result identity against the task and persisted worker job, applies semantic state changes, and persists the updated task atomically.

#### Scenario: Matching worker result is applied
- **WHEN** a persisted task has a completed worker job and the handler receives a `WorkerResult` with matching `task_id` and `worker_job_id`
- **THEN** the handler persists an updated `TaskState`
- **AND** the worker job ID appears in `TaskState.completed_worker_job_ids`
- **AND** the worker job is absent from `TaskState.active_worker_jobs`

#### Scenario: Mismatched task is rejected
- **WHEN** the handler receives a `WorkerResult` whose `task_id` does not match a persisted task or worker job task
- **THEN** the handler rejects the result without mutating any task state

#### Scenario: Duplicate result application is idempotent
- **WHEN** the handler receives a `WorkerResult` whose `worker_job_id` is already present in `TaskState.completed_worker_job_ids`
- **THEN** the handler returns the current task state without duplicating failures, assumptions, questions, artifacts, completed job IDs, or repair round increments

### Requirement: Handler projects produced artifacts into current task artifacts
The backend SHALL update `TaskState.current_artifacts` from `WorkerResult.produced_artifacts` using Router v1 artifact type semantics while requiring produced artifact IDs to belong to the same task.

#### Scenario: Development result updates current code and I/O contract
- **WHEN** the handler applies a completed `plc-dev` result with `outcome.status: "passed"` that produced `requirements_ir`, `plc_code`, and `io_contract` artifact refs
- **THEN** `TaskState.current_artifacts.requirements_ir` references the produced requirements artifact
- **AND** `TaskState.current_artifacts.current_code` references the produced PLC code artifact
- **AND** `TaskState.current_artifacts.current_io_contract` references the produced I/O contract artifact
- **AND** all produced artifact IDs are present in `TaskState.current_artifacts.all_artifact_ids`

#### Scenario: Test result updates latest test evidence
- **WHEN** the handler applies a completed `plc-test` result that produced a `test_report` artifact and optional `failing_trace` artifact
- **THEN** `TaskState.current_artifacts.latest_test_report` references the produced test report
- **AND** `TaskState.current_artifacts.latest_failing_trace` references the produced failing trace when one exists

#### Scenario: Formal result updates latest formal evidence
- **WHEN** the handler applies a completed `plc-formal` result that produced a `formal_report` artifact and optional `counterexample` artifact
- **THEN** `TaskState.current_artifacts.latest_formal_report` references the produced formal report
- **AND** `TaskState.current_artifacts.latest_counterexample` references the produced counterexample when one exists

#### Scenario: Repair result updates patch and patched code
- **WHEN** the handler applies a completed `plc-repair` result with `outcome.status: "passed"` that produced `patch`, `plc_code`, and `repair_summary` artifact refs
- **THEN** `TaskState.current_artifacts.latest_patch` references the produced patch
- **AND** `TaskState.current_artifacts.current_code` references the produced PLC code artifact
- **AND** `TaskState.current_artifacts.latest_repair_summary` references the produced repair summary

#### Scenario: Foreign artifact reference is rejected
- **WHEN** a `WorkerResult.produced_artifacts` entry references an artifact that does not exist or belongs to another task
- **THEN** the handler rejects the result without mutating task state

### Requirement: Handler updates gate flags from worker outcomes
The backend SHALL update `TaskState.gates` according to completed worker business outcomes and SHALL clear `can_finish_as_success` whenever worker result handling changes runtime evidence.

#### Scenario: Passing test clears regression requirement
- **WHEN** the handler applies a completed `plc-test` result with `outcome.status: "passed"`
- **THEN** `TaskState.gates.latest_test_passed` is true
- **AND** `TaskState.gates.regression_required` is false
- **AND** `TaskState.gates.can_finish_as_success` is false

#### Scenario: Failing test marks blocking failure state
- **WHEN** the handler applies a completed `plc-test` result with `outcome.status: "failed"` and a blocking test failure
- **THEN** `TaskState.gates.latest_test_passed` is false
- **AND** `TaskState.gates.has_blocking_failure` is true
- **AND** `TaskState.gates.can_finish_as_success` is false

#### Scenario: Passing formal verification clears formal regression requirement
- **WHEN** the handler applies a completed `plc-formal` result with `outcome.status: "passed"`
- **THEN** `TaskState.gates.latest_formal_passed` is true
- **AND** `TaskState.gates.formal_regression_required` is false
- **AND** `TaskState.gates.can_finish_as_success` is false

#### Scenario: Failing formal verification marks blocking failure state
- **WHEN** the handler applies a completed `plc-formal` result with `outcome.status: "failed"` and a blocking formal failure
- **THEN** `TaskState.gates.latest_formal_passed` is false
- **AND** `TaskState.gates.has_blocking_failure` is true
- **AND** `TaskState.gates.can_finish_as_success` is false

#### Scenario: Successful repair requires regression
- **WHEN** the handler applies a completed `plc-repair` result with `outcome.status: "passed"` and a patched code artifact
- **THEN** `TaskState.runtime_limits.repair_rounds` increases by one
- **AND** `TaskState.gates.regression_required` is true
- **AND** previous test and formal pass markers for the old code are invalidated
- **AND** `TaskState.gates.can_finish_as_success` is false

#### Scenario: Repair after formal failure requires formal regression
- **WHEN** the handler applies a completed `plc-repair` result with `outcome.status: "passed"` while the task has an open blocking formal failure
- **THEN** `TaskState.gates.formal_regression_required` is true

### Requirement: Handler manages failures and clarification requests
The backend SHALL merge worker-provided failures and clarification questions into `TaskState` while preserving evidence references and deduplicating by stable IDs.

#### Scenario: Worker failures are appended once
- **WHEN** the handler applies a completed failed worker result containing failures not already present in task state
- **THEN** those failures are appended to `TaskState.failures`
- **AND** each failure keeps its source, severity, status, evidence artifact IDs, and creating worker job ID

#### Scenario: Passing validator resolves same-source open failures
- **WHEN** the handler applies a completed passing `plc-test` or `plc-formal` result
- **THEN** open blocking failures from the same source are marked `resolved`
- **AND** resolved failures record the passing worker job ID and the latest evidence artifact ID when available
- **AND** `TaskState.gates.has_blocking_failure` reflects whether any open blocking failures remain

#### Scenario: Repair does not resolve failures by itself
- **WHEN** the handler applies a completed passing `plc-repair` result
- **THEN** existing open blocking failures remain open until a later test or formal result proves they are resolved

#### Scenario: Worker clarification pauses task
- **WHEN** the handler applies a completed worker result with `outcome.status: "need_clarification"` and a blocking clarification request
- **THEN** the clarification questions are added to `TaskState.unresolved_questions`
- **AND** the task status becomes `waiting_user`
- **AND** the task phase becomes `clarifying`

### Requirement: Handler handles non-success execution statuses conservatively
The backend SHALL avoid applying normal worker-specific pass/fail semantics for `WorkerResult.execution_status` values other than `completed`.

#### Scenario: Timeout result completes job tracking without semantic artifact updates
- **WHEN** the handler applies a `WorkerResult` with `execution_status: "timeout"`
- **THEN** the worker job ID is removed from active worker jobs and added to completed worker job IDs
- **AND** `TaskState.current_artifacts`, `TaskState.failures`, and test/formal pass markers are not changed from the timeout result

#### Scenario: Error result completes job tracking without creating domain failure
- **WHEN** the handler applies a `WorkerResult` with `execution_status: "error"`
- **THEN** the worker job ID is removed from active worker jobs and added to completed worker job IDs
- **AND** no new domain `Failure` is added unless the result already contains a valid failure payload
