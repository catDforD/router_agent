## ADDED Requirements

### Requirement: Runtime supports tool-loop orchestration without standalone intake classification
The backend SHALL support Main Agent execution for newly created tasks without requiring a separate structured intake classification model call.

#### Scenario: Created task can start Main Agent tool loop
- **WHEN** Runtime starts execution for a task with `status` equal to `created`, `phase` equal to `intake`, `task_type` equal to `unknown`, and `difficulty.level` equal to `L0`
- **THEN** Runtime may start the Main Agent tool-loop orchestration directly
- **AND** no structured classification object is required before the first Main Agent model turn

#### Scenario: Worker dispatch still requires guarded runnable state
- **WHEN** the Main Agent proposes a worker call for a task that lacks required inputs or violates runtime policy
- **THEN** Scheduler Guard or the worker tool rejects the call
- **AND** no worker side effects are created by the rejected call

### Requirement: Clarification is requested through Main Agent tools
The backend SHALL pause task execution for missing information through an explicit clarification tool rather than a structured intake classification output.

#### Scenario: Clarification tool asks user for missing information
- **WHEN** the Main Agent calls the clarification tool with at least one required question
- **THEN** the persisted task has `status` equal to `waiting_user`, `phase` equal to `clarifying`, and open unresolved clarification questions
- **AND** Runtime does not create a PLC worker job for that clarification path

#### Scenario: Clarification without questions is rejected
- **WHEN** the Main Agent calls the clarification tool without any required question
- **THEN** Runtime rejects the clarification request
- **AND** does not update the task state from that invalid request

## REMOVED Requirements

### Requirement: Runtime classifies intake tasks before worker execution
**Reason**: Runtime no longer requires a separate structured intake classification phase before Main Agent orchestration.
**Migration**: Start the Main Agent tool loop directly, use public plan and clarification tools, and rely on Scheduler Guard and Quality Gate for enforcement.

### Requirement: Intake classification output is structured
**Reason**: Main Agent production execution no longer depends on structured model output or `response_format`.
**Migration**: Persist planning, clarification, worker, gate, and finalization decisions through explicit tools and Router events.

### Requirement: Runtime applies validated classification to TaskState
**Reason**: Task planning state is no longer driven by a standalone classification object.
**Migration**: Use tool-driven plan updates, clarification updates, worker results, gate results, and finalization tools to mutate persisted state.

### Requirement: Runtime elevates safety-critical gates
**Reason**: Safety and completion enforcement must move from a structured classification decision into deterministic tool, gate, and finalization validation.
**Migration**: Enforce required test/formal evidence through Scheduler Guard, worker tool policy, Quality Gate, and finalization checks.

### Requirement: Clarification-required classification pauses worker execution
**Reason**: Clarification is now requested by an explicit Main Agent tool rather than a classification result.
**Migration**: Use `request_clarification` to persist questions and move tasks to `waiting_user`.

### Requirement: Intake classification is observable
**Reason**: Intake classification events are replaced by public plan, clarification, message, and tool events from the Main Agent tool loop.
**Migration**: Observe task setup through `main_agent.message`, `main_agent.plan_updated`, `main_agent.clarification_requested`, and related task events.
