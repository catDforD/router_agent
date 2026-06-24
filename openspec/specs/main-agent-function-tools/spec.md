# main-agent-function-tools Specification

## Purpose
Expose the current OpenAI-compatible Main Agent tool surface while keeping
Runtime policy, artifact boundaries, worker dispatch, and finalization
authoritative in backend services.

## Requirements

### Requirement: Default Main Agent tools are generic execution tools
The backend SHALL expose a Codex-like generic tool set to the default Main Agent tool loop.

#### Scenario: Tool set is available for Main Agent registration
- **WHEN** the Main Agent service constructs its model-facing tool list
- **THEN** the backend exposes tools named `list_files`, `read_file`, `write_file`, `apply_patch`, `exec_command`, `git_status`, `read_artifact`, `write_artifact`, and `call_mcp_tool`
- **AND** the backend does not expose `finish_task` to the model

#### Scenario: Tools can be invoked without a live provider
- **WHEN** a developer invokes an agent tool service method directly from unit tests or a local development script
- **THEN** the tool executes through the same runtime service implementation used by the provider-facing tool-loop wrapper

### Requirement: Planning and clarification helpers remain available
The backend SHALL keep service-level helpers that deterministic runners can use to persist plans and clarification requests without relying on a structured Intake output.

#### Scenario: Plan update prepares task state
- **WHEN** `update_plan` is invoked with a persisted task and public plan summary
- **THEN** the backend emits an observable plan update
- **AND** may move a created intake task to `running` and `planning` with normalized task context

#### Scenario: Clarification tool pauses task
- **WHEN** `request_clarification` is invoked with one or more required questions
- **THEN** the backend persists open clarification questions
- **AND** moves the task to `waiting_user` and `clarifying`
- **AND** emits user-visible Main Agent and task waiting events
- **AND** no worker job is created by the clarification path

### Requirement: MCP wrapper routes domain worker calls
The backend SHALL let the default tool loop call configured domain workers through `call_mcp_tool`.

#### Scenario: Domain worker tool prepares created intake task
- **WHEN** `call_mcp_tool` is invoked for a configured PLC worker tool and the task is still `created`, in `intake`, or has task type `unknown`
- **THEN** the backend prepares the task context for worker dispatch
- **AND** emits a `task.updated` event before worker lifecycle events

#### Scenario: Unsupported MCP tool is rejected
- **WHEN** `call_mcp_tool` is invoked with an unsupported tool name
- **THEN** the backend returns a rejected tool result
- **AND** no worker job, worker event, artifact, or task mutation is created by that invocation

### Requirement: Worker dispatch remains guarded
The backend SHALL require worker dispatch helpers to load the latest persisted `TaskState`, select input artifacts, and pass Scheduler Guard validation before creating worker side effects.

#### Scenario: Prepared development task can call PLC development
- **WHEN** a configured PLC development worker is invoked for a persisted runnable task whose selected input artifacts include `raw_user_request` or `requirements_ir`
- **THEN** the tool validates the proposed worker call
- **AND** invokes the MCP adapter with a valid `plc-dev` `WorkerInput`
- **AND** applies the returned `WorkerResult` through WorkerResult Handler

#### Scenario: Unprepared intake task worker call is rejected without mutation
- **WHEN** any direct worker helper is invoked for a task whose status is `created`, phase is `intake`, or task type is `unknown`
- **THEN** the helper returns a rejected result containing the Scheduler Guard violation
- **AND** no worker job, worker event, artifact, or task mutation is created by that invocation

#### Scenario: Test without current code is rejected
- **WHEN** `plc-test` is invoked for a task without `TaskState.current_artifacts.current_code`
- **THEN** the tool returns a rejected result containing the Scheduler Guard violation
- **AND** no worker side effects are created

#### Scenario: Repair without open blocking failure is rejected
- **WHEN** `plc-repair` is invoked for a task with no open blocking failure
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

### Requirement: Quality Gate and final report tools keep durable audit trail
The backend SHALL keep service methods for running Quality Gate and writing final reports while runtime lifecycle remains authoritative for terminal state.

#### Scenario: Quality Gate tool persists audit trail
- **WHEN** `run_quality_gate` is invoked for a persisted task
- **THEN** the tool runs the Quality Gate service
- **AND** returns the aggregate assessment status, blocking flag, failed gate names, and gate report artifact reference

#### Scenario: Final response writes final report
- **WHEN** the default tool loop stops with an allowed final response
- **THEN** runtime writes a `final_report` artifact and a `main_agent_log` artifact
- **AND** records `agent.completed` before the terminal task event
