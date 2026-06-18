# main-agent-service Specification

## Purpose
TBD - created by archiving change add-main-agent-service. Update Purpose after archive.
## Requirements
### Requirement: Main Agent service runs task episodes
The backend SHALL provide a Main Agent service that runs one episode for a persisted Router task while reusing existing runtime services for state, artifacts, events, guarded tools, and quality gates.

#### Scenario: Episode starts for existing task
- **WHEN** `MainAgentService.run_episode` is invoked for an existing task
- **THEN** the service loads the latest persisted `TaskState`
- **AND** creates or reuses an `openai_trace_id`
- **AND** creates a new `main_agent_run_id`
- **AND** persists both IDs on `TaskState.trace`
- **AND** emits a `main_agent.started` event correlated with the trace and run IDs

#### Scenario: Missing task is rejected
- **WHEN** `MainAgentService.run_episode` is invoked for a task ID that does not exist
- **THEN** the service returns or raises a not-found failure without creating Main Agent events, worker jobs, artifacts, or task mutations

#### Scenario: Terminal task is not re-run
- **WHEN** `MainAgentService.run_episode` is invoked for a task whose status is `succeeded`, `partial_failed`, `failed`, or `cancelled`
- **THEN** the service does not start a model run or worker dispatch
- **AND** the persisted task remains terminal

### Requirement: Main Agent service uses compact state views
The backend SHALL build compact Main Agent state views from `TaskState` and artifact references instead of embedding large artifact contents in model input.

#### Scenario: State view contains scheduling facts
- **WHEN** the Main Agent service builds a state view for a task
- **THEN** the view includes task identity, user goal, normalized goal, status, phase, task type, difficulty summary, gate flags, current artifact references, open failure summaries, repair round counters, worker call counters, and available tools

#### Scenario: State view excludes large content
- **WHEN** the Main Agent service builds a state view for a task with PLC code, test report, formal report, counterexample, patch, or worker log artifacts
- **THEN** the view includes artifact IDs, types, versions, URIs, hashes, and summaries
- **AND** it does not include full PLC code, full reports, full counterexample content, full patches, or full logs

### Requirement: Main Agent service classifies intake tasks before worker orchestration
The backend SHALL run and apply a structured intake classification before orchestration when a task is still `created`, in `intake`, or has task type `unknown`.

#### Scenario: Created intake task is classified first
- **WHEN** the service runs an episode for a task with status `created`, phase `intake`, task type `unknown`, and difficulty level `L0`
- **THEN** the service obtains a validated intake classification output before invoking any PLC worker tool
- **AND** no worker job is created before the classification has been applied

#### Scenario: Classification moves ready task to planning
- **WHEN** the intake classification output does not require clarification
- **THEN** the service persists normalized goal, task type, difficulty profile, and gate requirements on `TaskState`
- **AND** the task status becomes `running`
- **AND** the task phase becomes `planning`
- **AND** the service emits `main_agent.decision` and `task.updated` events summarizing the classification

#### Scenario: Classification pauses for clarification
- **WHEN** the intake classification output requires clarification and contains at least one required question
- **THEN** the task status becomes `waiting_user`
- **AND** the task phase becomes `clarifying`
- **AND** the open clarification questions are persisted on `TaskState.unresolved_questions`
- **AND** the service emits `main_agent.clarification_requested` and `task.waiting_user` events
- **AND** no PLC worker job is created

#### Scenario: Safety-critical classification is elevated
- **WHEN** the intake classification output contains safety-critical signals such as emergency stop, interlock, fault latching, mode switching, state machine, or safety constraints
- **THEN** the persisted task difficulty is at least `L3`
- **AND** `difficulty.requires_test` is true
- **AND** `difficulty.requires_formal` is true
- **AND** `gates.test_required` is true
- **AND** `gates.formal_required` is true

### Requirement: Main Agent service orchestrates through existing function tools
The backend SHALL expose existing Main Agent function tools to the orchestration agent and SHALL rely on those tools for worker dispatch, artifact reads, quality gate execution, and task completion.

#### Scenario: Tool registration uses existing tool set
- **WHEN** the Main Agent service constructs the orchestration agent
- **THEN** the agent tools include `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_parallel_workers`, `read_artifact`, `run_quality_gate`, and `finish_task`

#### Scenario: Ordinary development happy path completes
- **WHEN** a classified `new_plc_development` task requires tests but not formal verification and the mock scenario passes development and tests
- **THEN** the episode dispatches PLC development through the existing worker tool path
- **AND** dispatches PLC testing through the existing worker tool path
- **AND** runs Quality Gate through `run_quality_gate`
- **AND** finishes through `finish_task`
- **AND** the persisted task status becomes `succeeded`

#### Scenario: Safety-critical development includes formal verification
- **WHEN** a classified `new_plc_development` task requires tests and formal verification
- **THEN** the episode dispatches PLC development
- **AND** dispatches PLC testing and PLC formal verification before successful finish
- **AND** the persisted task has passing latest test and formal markers before `finish_task` succeeds

#### Scenario: Failed validation triggers repair and regression
- **WHEN** a worker result creates an open blocking test or formal failure and repair rounds remain available
- **THEN** the episode dispatches PLC repair through `call_plc_repair`
- **AND** dispatches regression testing after repair
- **AND** dispatches formal regression when a formal failure was repaired
- **AND** successful finish is not allowed until open blocking failures and regression flags are cleared

### Requirement: Main Agent instructions enforce artifact-oriented scheduling policy
The backend SHALL provide Main Agent instructions that describe Router scheduling policy, artifact boundaries, guard expectations, and finalization rules.

#### Scenario: Instructions require guarded finalization
- **WHEN** the orchestration agent receives instructions
- **THEN** the instructions tell the agent to run `run_quality_gate` before `finish_task`
- **AND** tell the agent not to mark success when tool results report guard violations, open blocking failures, missing required tests, missing required formal verification, or pending regression

#### Scenario: Instructions preserve artifact boundaries
- **WHEN** the orchestration agent receives instructions
- **THEN** the instructions tell the agent to return and discuss artifact references and summaries instead of copying full code, reports, logs, or counterexamples into its final output

### Requirement: Main Agent service returns structured episode output
The backend SHALL represent episode outcomes with a structured output that summarizes decisions, plans, tool outcomes, artifact references, gate status, and final task status.

#### Scenario: Successful episode output is structured
- **WHEN** a Main Agent episode completes successfully
- **THEN** the returned output includes the task ID, main agent run ID, final task status, decisions, plan summary, artifact references, gate summary, and next recommended action

#### Scenario: Waiting-user episode output is structured
- **WHEN** an episode pauses for clarification
- **THEN** the returned output includes final task status `waiting_user`, open clarification question IDs, and next recommended action `ask_user`

#### Scenario: Max turns failure is observable
- **WHEN** the OpenAI Agents SDK runner exceeds the configured maximum turns
- **THEN** the service records an observable Main Agent failure result
- **AND** does not mark the task `succeeded`
- **AND** does not bypass Scheduler Guard or Quality Gate

### Requirement: Main Agent service is testable without live model calls
The backend SHALL keep Main Agent service behavior testable without requiring a live OpenAI API call.

#### Scenario: Fake runner drives deterministic integration test
- **WHEN** tests provide a fake runner that returns structured classification and orchestration outputs
- **THEN** `MainAgentService.run_episode` uses those outputs to exercise persisted state transitions, events, guarded tools, mock worker artifacts, Quality Gate, and finish behavior
- **AND** the test does not require `OPENAI_API_KEY`

#### Scenario: Production runner uses OpenAI Agents SDK boundary
- **WHEN** the service is configured for production model execution
- **THEN** it constructs OpenAI Agents SDK `Agent` instances with instructions, tools, output types, runtime context, max turns, and run config tracing metadata

