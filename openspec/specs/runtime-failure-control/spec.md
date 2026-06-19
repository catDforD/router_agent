# runtime-failure-control Specification

## Purpose
TBD - created by archiving change harden-timeout-cancel-error-normalization. Update Purpose after archive.
## Requirements
### Requirement: Runtime uses soft cancellation as a terminal Router state
The backend SHALL treat user cancellation as a terminal Router-side state that prevents future Runtime execution, worker dispatch, and finalization for the task.

#### Scenario: Cancellable task is cancelled
- **WHEN** `POST /api/tasks/{task_id}/cancel` is accepted for a task whose status is `created`, `running`, or `waiting_user`
- **THEN** the persisted `TaskState.status` SHALL be `cancelled`
- **AND** `TaskState.phase` SHALL be `completed`
- **AND** `TaskState.completed_at` SHALL be set
- **AND** the task event log SHALL contain one user-visible `task.cancelled` event

#### Scenario: Cancellation clears runtime worker activity from task state
- **WHEN** a task is cancelled while `TaskState.active_worker_jobs` or `runtime_limits.active_parallel_workers` indicate active work
- **THEN** the cancelled `TaskState` SHALL have no active worker job refs
- **AND** `runtime_limits.active_parallel_workers` SHALL be `0`
- **AND** worker lifecycle audit SHALL remain available through `worker_jobs` and worker events

#### Scenario: Cancelled task is not scheduled again
- **WHEN** a scheduled Runtime start or resume runs after a task has been cancelled
- **THEN** Runtime SHALL skip the invocation without starting a Main Agent episode
- **AND** Runtime SHALL NOT create a new worker job, worker event, or worker artifact

#### Scenario: Cancelled task is not overwritten by stale finalization
- **WHEN** a stale Main Agent episode or tool path attempts to finish a cancelled task as `succeeded`, `partial_failed`, or `failed`
- **THEN** the finish attempt SHALL be rejected or no-op
- **AND** the persisted task status SHALL remain `cancelled`

### Requirement: Late worker results after terminal task state are audit-only
The backend SHALL preserve worker job and event audit trails for in-flight worker results that complete after a task reaches a terminal state, but SHALL NOT project those results into terminal task business state.

#### Scenario: Late worker result completes audit trail after cancellation
- **WHEN** a worker job that started before cancellation returns after the task is already `cancelled`
- **THEN** the `worker_jobs` row SHALL contain the terminal worker result and terminal job status
- **AND** the task event log SHALL contain the corresponding `worker.completed`, `worker.timeout`, or `worker.error` event

#### Scenario: Late worker result does not mutate cancelled task state
- **WHEN** `WorkerResultHandler` receives a result for a task whose status is `cancelled`
- **THEN** it SHALL NOT update `TaskState.current_artifacts`, `TaskState.gates`, `TaskState.failures`, `TaskState.assumptions`, `TaskState.unresolved_questions`, `TaskState.completed_worker_job_ids`, `TaskState.phase`, or `TaskState.status`
- **AND** the handler result SHALL indicate that no business-state projection was applied

#### Scenario: Late worker result does not revive any terminal task
- **WHEN** `WorkerResultHandler` receives a result for a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** the persisted task SHALL remain in its existing terminal status
- **AND** no produced artifact SHALL become the current task artifact because of that late result

### Requirement: Worker dispatch failures are normalized and diagnosable
The backend SHALL normalize worker timeout, MCP connection, worker schema, and worker execution failures into canonical Router worker result, worker job, and event records after worker dispatch starts.

#### Scenario: Worker timeout is normalized
- **WHEN** a mock or real worker invocation times out
- **THEN** the returned `WorkerResult.execution_status` SHALL be `timeout`
- **AND** `WorkerResult.error.error_code` SHALL be `MCP_TIMEOUT`
- **AND** `WorkerResult.error.retryable` SHALL be `true`
- **AND** the corresponding `worker_jobs.status` SHALL be `timeout`
- **AND** the task event log SHALL contain a user-visible `worker.timeout` event correlated with the worker job ID

#### Scenario: MCP connection failure is normalized
- **WHEN** a real MCP worker cannot be reached after dispatch starts
- **THEN** the returned `WorkerResult.execution_status` SHALL be `error`
- **AND** `WorkerResult.error.error_code` SHALL be `MCP_CONNECTION_ERROR`
- **AND** the corresponding `worker_jobs.status` SHALL be `error`
- **AND** the task event log SHALL contain a user-visible `worker.error` event without exposing secret configuration values

#### Scenario: Invalid worker output is normalized
- **WHEN** mock, real, or draft worker output cannot be validated against the expected Router worker output shape
- **THEN** the returned `WorkerResult.execution_status` SHALL be `error`
- **AND** `WorkerResult.error.error_code` SHALL be `WORKER_SCHEMA_INVALID`
- **AND** the corresponding `worker_jobs.status` SHALL be `error`
- **AND** no unvalidated worker artifact SHALL be projected into `TaskState.current_artifacts`

#### Scenario: Worker execution exception is normalized
- **WHEN** a worker invocation raises an unexpected execution exception after dispatch starts
- **THEN** the returned `WorkerResult.execution_status` SHALL be `error`
- **AND** `WorkerResult.error.error_code` SHALL be `WORKER_EXECUTION_ERROR`
- **AND** the corresponding `worker_jobs.status` SHALL be `error`
- **AND** the task event log SHALL contain a user-visible `worker.error` event

### Requirement: Guard and control-plane limits do not create hanging tasks
The backend SHALL make Scheduler Guard rejections and Main Agent control-plane exhaustion observable without creating fake worker executions or leaving tasks indefinitely running.

#### Scenario: Worker call budget exhaustion is guard-rejected before dispatch
- **WHEN** the Main Agent tool path attempts to dispatch a worker after `runtime_limits.worker_calls_used` has reached `runtime_limits.max_worker_calls`
- **THEN** Scheduler Guard SHALL reject the action before worker dispatch
- **AND** no worker job, worker event, or worker artifact SHALL be created for the rejected action
- **AND** a visible Main Agent tool-result or decision event SHALL identify the guard violation code

#### Scenario: Main Agent max turns terminalizes non-terminal task
- **WHEN** Main Agent execution exceeds the configured maximum turns for a non-terminal task
- **THEN** the backend SHALL record a visible Main Agent error with error code `MAIN_AGENT_MAX_TURNS_EXCEEDED`
- **AND** the task SHALL NOT be marked `succeeded`
- **AND** the task SHALL move to a terminal failed state unless it was already terminal

#### Scenario: Control-plane failure preserves existing terminal task
- **WHEN** a Main Agent max-turn or model-behavior error is observed after the task has already become terminal
- **THEN** the backend SHALL NOT overwrite the existing terminal task status
- **AND** the error SHALL remain visible in Main Agent output or events
