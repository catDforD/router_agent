# main-agent-function-tools Specification

## Purpose
TBD - created by archiving change add-main-agent-function-tools. Update Purpose after archive.
## Requirements
### Requirement: Main Agent tools expose guarded runtime actions
The backend SHALL provide Main Agent function tools for PLC worker dispatch, parallel worker dispatch, artifact reading, Quality Gate execution, and task completion while keeping Runtime policy authoritative.

#### Scenario: Tool set is available for Main Agent registration
- **WHEN** the Main Agent service constructs its tool list
- **THEN** the backend exposes tools named `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_parallel_workers`, `read_artifact`, `run_quality_gate`, and `finish_task`

#### Scenario: Tools can be invoked without a Main Agent run
- **WHEN** a developer invokes an agent tool directly from unit tests or a local development script
- **THEN** the tool executes through the same runtime service path used by the SDK-facing function tool wrapper

### Requirement: Worker tools dispatch only validated classified tasks
The backend SHALL require worker tools to load the latest persisted `TaskState`, select input artifacts, and pass Scheduler Guard validation before creating worker side effects.

#### Scenario: Classified development task can call PLC development
- **WHEN** `call_plc_dev` is invoked for a persisted running task whose task type is classified and whose selected input artifacts include `raw_user_request` or `requirements_ir`
- **THEN** the tool validates the proposed worker call
- **AND** invokes the MCP adapter with a valid `plc-dev` `WorkerInput`
- **AND** applies the returned `WorkerResult` through WorkerResult Handler

#### Scenario: Intake task worker call is rejected without mutation
- **WHEN** any worker tool is invoked for a task whose status is `created`, phase is `intake`, or task type is `unknown`
- **THEN** the tool returns a rejected result containing the Scheduler Guard violation
- **AND** no worker job, worker event, artifact, or task mutation is created by that tool invocation

#### Scenario: Test without current code is rejected
- **WHEN** `call_plc_test` is invoked for a task without `TaskState.current_artifacts.current_code`
- **THEN** the tool returns a rejected result containing the Scheduler Guard violation
- **AND** no worker side effects are created

#### Scenario: Repair without open blocking failure is rejected
- **WHEN** `call_plc_repair` is invoked for a task with no open blocking failure
- **THEN** the tool returns a rejected result containing the Scheduler Guard violation
- **AND** no worker side effects are created

### Requirement: Worker input builder constructs Router worker inputs deterministically
The backend SHALL provide a worker input builder that constructs valid Router v1 `WorkerInput` payloads from a persisted `TaskState`, worker type, selected input artifacts, objective, and trace context.

#### Scenario: Builder selects development inputs
- **WHEN** the builder constructs input for `plc-dev`
- **THEN** the input artifacts include the current `raw_user_request` artifact when available, or the current `requirements_ir` artifact when requirements are already available
- **AND** the built `WorkerInput` validates against the Router v1 model

#### Scenario: Builder selects validator inputs
- **WHEN** the builder constructs input for `plc-test` or `plc-formal`
- **THEN** the input artifacts include the current `requirements_ir` and current `plc_code` artifact references
- **AND** the built `WorkerInput` validates against the Router v1 model

#### Scenario: Builder selects repair inputs
- **WHEN** the builder constructs input for `plc-repair`
- **THEN** the input artifacts include the current `plc_code` artifact reference and at least one latest test or formal failure evidence artifact reference
- **AND** the built `WorkerInput` validates against the Router v1 model

#### Scenario: Builder populates worker context
- **WHEN** the builder constructs any worker input
- **THEN** the input context includes the task goal, task type, difficulty level, target PLC context, repair round, assumptions, and selected open failure IDs when applicable

### Requirement: Worker tools maintain dispatch counters and active job tracking
The backend SHALL update task worker tracking around successful worker dispatch attempts so Scheduler Guard concurrency and worker call budget checks remain meaningful.

#### Scenario: Worker dispatch records active job before adapter call
- **WHEN** a worker tool passes Scheduler Guard validation and is about to call the MCP adapter
- **THEN** the task state records a matching `active_worker_jobs` entry
- **AND** increments `runtime_limits.active_parallel_workers`
- **AND** increments `runtime_limits.worker_calls_used`

#### Scenario: Worker result handling clears active job state
- **WHEN** the MCP adapter returns a terminal `WorkerResult` and WorkerResult Handler applies it
- **THEN** the worker job is absent from `TaskState.active_worker_jobs`
- **AND** the worker job ID appears in `TaskState.completed_worker_job_ids`
- **AND** `runtime_limits.active_parallel_workers` is decremented back toward the pre-dispatch count

#### Scenario: Dispatch error restores active counters
- **WHEN** a worker tool fails before receiving a terminal `WorkerResult`
- **THEN** the tool restores active worker tracking so the task does not retain a leaked active worker count or active job reference

### Requirement: Parallel worker tool validates the batch atomically
The backend SHALL provide a parallel worker tool that validates an entire proposed batch before dispatching any worker in that batch.

#### Scenario: Valid test and formal batch dispatches
- **WHEN** `run_parallel_workers` is invoked for a classified task with current requirements and code and a batch containing `plc-test` and `plc-formal`
- **THEN** the tool validates the batch with Scheduler Guard
- **AND** dispatches each worker through the same worker tool runtime path
- **AND** returns one compact result per worker

#### Scenario: Invalid parallel batch has no side effects
- **WHEN** `run_parallel_workers` is invoked with any worker call that violates Scheduler Guard policy
- **THEN** the tool returns a rejected result for the batch
- **AND** no worker job, worker event, artifact, or task mutation is created by that batch invocation

#### Scenario: Parallel repair is rejected
- **WHEN** `run_parallel_workers` is invoked with a batch containing `plc-repair`
- **THEN** the tool returns a rejected result containing the Scheduler Guard violation
- **AND** no worker side effects are created

### Requirement: Tool results are compact and artifact-oriented
The backend SHALL return structured tool results that summarize runtime outcomes without embedding large artifact contents in Main Agent context.

#### Scenario: Worker tool returns compact result
- **WHEN** a worker tool completes a worker invocation
- **THEN** the returned result includes status, summary, produced artifact references, failure summaries, gate state summary, and next recommended action
- **AND** the returned result does not include full PLC code, full test logs, full formal reports, full counterexample content, or full worker logs

#### Scenario: Guard rejection returns structured violation
- **WHEN** a tool invocation is rejected by Scheduler Guard
- **THEN** the returned result includes the guard violation code, message, and details
- **AND** the result status indicates rejection

### Requirement: Artifact read tool supports summary and bounded full modes
The backend SHALL provide `read_artifact` with `summary` and bounded `full` modes for artifacts belonging to the requested task.

#### Scenario: Summary mode returns metadata only
- **WHEN** `read_artifact` is invoked in `summary` mode for an artifact belonging to the task
- **THEN** the tool returns artifact metadata, artifact reference fields, summary, MIME type, size, and content hash
- **AND** it does not return artifact content

#### Scenario: Full mode returns bounded content
- **WHEN** `read_artifact` is invoked in `full` mode for a UTF-8 text artifact belonging to the task
- **THEN** the tool returns content no larger than the configured maximum character limit
- **AND** the result indicates whether the content was truncated

#### Scenario: Foreign artifact read is rejected
- **WHEN** `read_artifact` is invoked for an artifact that does not belong to the requested task
- **THEN** the tool returns a rejected or not-found result
- **AND** it does not return artifact content

### Requirement: Quality Gate and finish tools enforce final delivery policy
The backend SHALL provide tools for running Quality Gate and finishing tasks through existing runtime services and Scheduler Guard policy.

#### Scenario: Quality Gate tool persists audit trail
- **WHEN** `run_quality_gate` is invoked for a persisted task
- **THEN** the tool runs the Quality Gate service
- **AND** returns the aggregate assessment status, blocking flag, failed gate names, and gate report artifact reference

#### Scenario: Finish succeeded requires passing Quality Gate
- **WHEN** `finish_task` is invoked with final status `succeeded` and Scheduler Guard rejects successful completion
- **THEN** the tool returns a rejected result containing the guard violation
- **AND** the task is not marked terminal

#### Scenario: Finish succeeded marks terminal task
- **WHEN** `finish_task` is invoked with final status `succeeded` for a task that passes Scheduler Guard completion validation
- **THEN** the tool persists task status `succeeded`
- **AND** sets phase `completed`
- **AND** sets `completed_at`
- **AND** emits a `task.succeeded` event

### Requirement: Main Agent tool calls include public rationale summaries
The backend SHALL allow Main Agent function tool invocations to carry a bounded public rationale summary that explains why the model selected the tool.

#### Scenario: Worker tool receives rationale summary
- **WHEN** the orchestration model invokes `call_plc_dev`, `call_plc_test`, `call_plc_formal`, or `call_plc_repair`
- **THEN** the tool invocation can include a `rationale_summary` value
- **AND** the backend records that value in Main Agent observability events and replay logs without forwarding it as hidden chain-of-thought

#### Scenario: Parallel worker tool receives rationale summary
- **WHEN** the orchestration model invokes `run_parallel_workers`
- **THEN** the tool invocation can include a `rationale_summary` value for the batch decision
- **AND** the backend records the value with the batch tool-call observability event

#### Scenario: Quality Gate tool receives rationale summary
- **WHEN** the orchestration model invokes `run_quality_gate`
- **THEN** the tool invocation can include a `rationale_summary` value explaining why the available evidence is ready for gate evaluation

### Requirement: Tool observability preserves compact tool results
The backend SHALL use existing compact tool result objects as the basis for Main Agent tool-result observability.

#### Scenario: Worker result is normalized for observability
- **WHEN** a worker tool returns a compact result to the Main Agent
- **THEN** the backend records result status, summary, artifact references, failure summaries, gate state summary, and next recommended action in the replay log
- **AND** emits only bounded summary fields and IDs in the user-visible event

#### Scenario: Guard rejection is observable
- **WHEN** a tool invocation is rejected by Scheduler Guard
- **THEN** the backend records the rejected status, guard violation code, message, and details in the replay log
- **AND** emits a compact user-visible `main_agent.tool_result` event

### Requirement: Main Agent orchestration finalization is report-first
The backend SHALL direct Main Agent orchestration to return a final structured output after Quality Gate instead of relying on a terminal `finish_task(succeeded)` tool call as the primary success path.

#### Scenario: Orchestration final output replaces terminal success tool call
- **WHEN** Quality Gate has passed and no blocking failures remain
- **THEN** orchestration instructions tell the model to return a final `MainAgentEpisodeOutput` recommending success
- **AND** Runtime validates the output, writes report artifacts, emits `main_agent.completed`, and applies terminal success

#### Scenario: Direct finish tool remains guarded
- **WHEN** `finish_task` is invoked directly outside the model orchestration finalization path
- **THEN** the tool still enforces Scheduler Guard finalization policy before mutating terminal task status
