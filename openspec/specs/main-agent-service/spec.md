# main-agent-service Specification

## Purpose
Run Router Main Agent episodes through the default OpenAI-compatible tool loop
while reusing persisted task state, events, artifacts, guarded tools, and
runtime finalization.

## Requirements

### Requirement: Main Agent service runs task episodes
The backend SHALL provide a Main Agent service that runs one episode for a persisted Router task while reusing existing runtime services for state, artifacts, events, guarded tools, and quality gates.

#### Scenario: Episode starts for existing task
- **WHEN** `MainAgentService.run_episode` is invoked for an existing non-terminal task
- **THEN** the service loads the latest persisted `TaskState`
- **AND** creates or reuses an `openai_trace_id`
- **AND** creates a new `main_agent_run_id`
- **AND** persists both IDs on `TaskState.trace`
- **AND** emits a `main_agent.started` event correlated with the trace and run IDs

#### Scenario: Terminal task is not re-run
- **WHEN** `MainAgentService.run_episode` is invoked for a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** the service does not start a model run or worker dispatch
- **AND** the persisted task remains terminal

### Requirement: Main Agent service starts orchestration without standalone Intake
The backend SHALL begin Main Agent orchestration for created intake tasks without running a separate structured Intake classifier.

#### Scenario: Created intake task enters tool loop directly
- **WHEN** the service runs an episode for a task with status `created`, phase `intake`, task type `unknown`, and difficulty level `L0`
- **THEN** the service invokes `run_orchestration`
- **AND** the runner is not asked to execute a separate intake phase
- **AND** no standalone structured classification output is required before the first orchestration turn

#### Scenario: Clarification uses explicit runtime tool path
- **WHEN** a deterministic runner or service helper requests clarification with at least one required question
- **THEN** the task moves to `waiting_user` and `clarifying`
- **AND** unresolved clarification questions are persisted on `TaskState`
- **AND** no PLC worker job is created by that clarification path

### Requirement: Main Agent service uses compact state views
The backend SHALL build compact Main Agent state views from `TaskState` and artifact references instead of embedding large artifact contents in model input.

#### Scenario: State view contains scheduling facts
- **WHEN** the Main Agent service builds a state view for a task
- **THEN** the view includes task identity, user goal, normalized goal, status, phase, task type, difficulty summary, gate flags, current artifact references, open failure summaries, repair round counters, worker call counters, workspace state, execution policy, and available tools

#### Scenario: State view excludes large content
- **WHEN** the Main Agent service builds a state view for a task with PLC code, test report, formal report, counterexample, patch, or worker log artifacts
- **THEN** the view includes artifact IDs, types, versions, URIs, hashes, and summaries
- **AND** it does not include full PLC code, full reports, full counterexample content, full patches, or full logs

### Requirement: Main Agent service uses OpenAI-compatible tool loop
The backend SHALL use the OpenAI-compatible Chat Completions runner as the only production Main Agent provider.

#### Scenario: Production runner uses Chat Completions tools
- **WHEN** the service is configured for production model execution
- **THEN** it constructs Chat Completions requests with instructions, messages, tool definitions, runtime context, max turns, and provider settings
- **AND** it does not require OpenAI Agents SDK structured output support

#### Scenario: Tool registration uses default generic tool set
- **WHEN** the Main Agent service constructs the default tool loop
- **THEN** the model-facing tools include `list_files`, `read_file`, `write_file`, `apply_patch`, `exec_command`, `git_status`, `read_artifact`, `write_artifact`, and `call_mcp_tool`
- **AND** the model-facing tools do not include `finish_task`

#### Scenario: Domain worker dispatch is routed through MCP tool wrapper
- **WHEN** the default tool loop needs to call a PLC worker
- **THEN** it uses `call_mcp_tool` with a configured PLC/domain tool name
- **AND** the backend prepares a created intake task for worker dispatch before creating worker side effects

### Requirement: Main Agent service finalizes through runtime lifecycle
The backend SHALL treat natural assistant stop plus runtime validation as the default completion path for the OpenAI-compatible tool loop.

#### Scenario: Final response writes durable completion artifacts
- **WHEN** the model returns a final assistant message with no tool calls and runtime stop policy allows completion
- **THEN** the backend records `agent.final_response`
- **AND** writes final report and Main Agent log artifacts
- **AND** records `agent.completed`
- **AND** applies the terminal task event when a terminal final status is selected

#### Scenario: Stop without required evidence is blocked
- **WHEN** the model returns a final assistant message before required execution evidence exists
- **THEN** the backend records a stop-blocked event
- **AND** prompts the model to continue required work instead of terminalizing the task

### Requirement: Main Agent service returns structured episode output
The backend SHALL return a structured service output summarizing the persisted episode result for callers and deterministic tests.

#### Scenario: Successful episode output is structured
- **WHEN** a Main Agent episode completes successfully
- **THEN** the returned output includes the task ID, main agent run ID, final task status, artifact references, gate summary, and summary

#### Scenario: Waiting-user episode output is structured
- **WHEN** an episode pauses for clarification
- **THEN** the returned output includes final task status `waiting_user`, open clarification question IDs, and next recommended action `ask_user`

#### Scenario: Max turns failure is observable
- **WHEN** the OpenAI-compatible runner exceeds the configured maximum turns
- **THEN** the service records an observable Main Agent failure result
- **AND** does not mark the task `succeeded`
- **AND** does not bypass Scheduler Guard or Quality Gate

### Requirement: Main Agent service is testable without live model calls
The backend SHALL keep Main Agent service behavior testable without requiring a live provider API call.

#### Scenario: Fake runner drives deterministic tests
- **WHEN** tests provide a fake runner that implements `run_orchestration`
- **THEN** `MainAgentService.run_episode` uses that runner to exercise persisted state transitions, events, guarded tools, mock worker artifacts, Quality Gate, and runtime completion behavior
- **AND** the fake runner is not required to implement a separate intake phase
- **AND** the test does not require provider credentials
