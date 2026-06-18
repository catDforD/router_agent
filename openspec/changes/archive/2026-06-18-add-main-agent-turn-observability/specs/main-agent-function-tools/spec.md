## ADDED Requirements

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
