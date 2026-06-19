## ADDED Requirements

### Requirement: Main Agent service starts orchestration without standalone structured intake
The backend SHALL allow Main Agent orchestration to begin for a newly created task without first obtaining a separate structured intake classification output.

#### Scenario: Created task enters tool-loop orchestration
- **WHEN** `MainAgentService.run_episode` starts for a task with status `created`, phase `intake`, and task type `unknown`
- **THEN** the service starts a Main Agent tool-loop episode
- **AND** no separate intake model call is required before the first orchestration model turn

#### Scenario: Agent can request clarification through tools
- **WHEN** the Main Agent determines that required information is missing
- **THEN** it can call a clarification tool that persists open questions
- **AND** the task moves to `waiting_user`
- **AND** no PLC worker job is created by that clarification path

### Requirement: Main Agent service terminalizes only through guarded tools
The backend SHALL treat final report and terminal task mutation tools as the authoritative completion path for Main Agent episodes.

#### Scenario: Final report tool precedes terminal status
- **WHEN** the Main Agent intends to finish a task as `succeeded`, `partial_failed`, or `failed`
- **THEN** it must create or reference a durable final report artifact through a Router tool before terminal task status is applied

#### Scenario: Assistant text alone does not finish task
- **WHEN** the model emits assistant text that claims the task is complete
- **THEN** the backend does not mark the task terminal unless the guarded finalization tool path succeeds

## MODIFIED Requirements

### Requirement: Main Agent service orchestrates through existing function tools
The backend SHALL expose Main Agent tools to the orchestration runner and SHALL rely on those tools for planning, clarification, worker dispatch, artifact reads, Quality Gate execution, final report creation, and task completion.

#### Scenario: Tool registration uses tool-loop tool set
- **WHEN** the Main Agent service constructs the tool list
- **THEN** the tools include `update_plan`, `request_clarification`, `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_parallel_workers`, `read_artifact`, `run_quality_gate`, `write_final_report`, and `finish_task`

#### Scenario: Ordinary development happy path completes
- **WHEN** a mock development task requires tests but not formal verification and the mock scenario passes development and tests
- **THEN** the episode dispatches PLC development through the worker tool path
- **AND** dispatches PLC testing through the worker tool path
- **AND** runs Quality Gate through `run_quality_gate`
- **AND** writes a final report through `write_final_report`
- **AND** finishes through `finish_task`
- **AND** the persisted task status becomes `succeeded`

#### Scenario: Safety-critical development includes formal verification
- **WHEN** a task requires tests and formal verification because of safety-critical content or guarded finalization policy
- **THEN** the episode dispatches PLC development
- **AND** dispatches PLC testing and PLC formal verification before successful finish
- **AND** the persisted task has passing latest test and formal markers before `finish_task` succeeds

#### Scenario: Failed validation triggers repair and regression
- **WHEN** a worker result creates an open blocking test or formal failure and repair rounds remain available
- **THEN** the episode dispatches PLC repair through `call_plc_repair`
- **AND** dispatches regression testing after repair
- **AND** dispatches formal regression when a formal failure was repaired
- **AND** successful finish is not allowed until open blocking failures and regression flags are cleared

### Requirement: Main Agent service is testable without live model calls
The backend SHALL keep Main Agent service behavior testable without requiring a live provider API call.

#### Scenario: Fake tool-loop runner drives deterministic integration test
- **WHEN** tests provide a fake runner that emits assistant messages, tool calls, and terminal tool results
- **THEN** `MainAgentService.run_episode` uses those scripted steps to exercise persisted state transitions, events, guarded tools, mock worker artifacts, Quality Gate, final report generation, and finish behavior
- **AND** the test does not require `OPENAI_API_KEY`, Main Agent provider credentials, or `DEEPSEEK_API_KEY`

#### Scenario: Production runner uses OpenAI-compatible Chat Completions boundary
- **WHEN** the service is configured for production model execution
- **THEN** it constructs Chat Completions requests with instructions, messages, tool definitions, runtime context, max turns, and provider settings
- **AND** it does not require OpenAI Agents SDK structured output support

## REMOVED Requirements

### Requirement: Main Agent service classifies intake tasks before worker orchestration
**Reason**: The new production Main Agent path plans and decides execution through the normal tool loop instead of requiring a separate structured intake model output before orchestration.
**Migration**: Use tool-loop planning, clarification, guarded worker calls, Quality Gate, and finalization validation to establish and enforce task execution requirements.

### Requirement: Main Agent service returns structured episode output
**Reason**: Main Agent completion is now represented by durable tool side effects, final report artifacts, completion events, and terminal task state rather than a model-returned structured episode object.
**Migration**: Tests and services should inspect persisted task state, events, artifacts, gate results, and replay log entries instead of relying on `MainAgentEpisodeOutput` as the production completion contract.
