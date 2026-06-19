## ADDED Requirements

### Requirement: Main Agent tools support planning and clarification
The backend SHALL provide tools that let the Main Agent persist a public plan and request user clarification without relying on a structured intake output.

#### Scenario: Plan update emits observable event
- **WHEN** the Main Agent calls `update_plan` with ordered public plan steps
- **THEN** the backend persists a user-visible `main_agent.plan_updated` event
- **AND** the event payload contains bounded plan step summaries

#### Scenario: Clarification tool pauses task
- **WHEN** the Main Agent calls `request_clarification` with one or more required questions
- **THEN** the backend persists open clarification questions
- **AND** moves the task to `waiting_user` and `clarifying`
- **AND** emits user-visible Main Agent and task waiting events
- **AND** no worker job is created by the clarification tool

### Requirement: Main Agent tools support report-first terminalization
The backend SHALL provide tools for writing the final report and finishing a task while preserving Scheduler Guard and Quality Gate authority.

#### Scenario: Final report tool writes artifacts
- **WHEN** the Main Agent calls `write_final_report` with final status, summary, delivery references, validation summary, assumptions, and unresolved items
- **THEN** the backend writes a user-visible `FINAL_REPORT` artifact
- **AND** writes or updates a `MAIN_AGENT_LOG` artifact for the episode replay
- **AND** returns compact artifact references to the model

#### Scenario: Finish tool requires durable final report
- **WHEN** `finish_task` is invoked for `succeeded`, `partial_failed`, or `failed`
- **THEN** the tool verifies that the required final report artifact exists or is referenced
- **AND** rejects terminal mutation when report durability cannot be proven

## MODIFIED Requirements

### Requirement: Main Agent tools expose guarded runtime actions
The backend SHALL provide Main Agent tools for public planning, clarification, PLC worker dispatch, parallel worker dispatch, artifact reading, Quality Gate execution, final report writing, and task completion while keeping Runtime policy authoritative.

#### Scenario: Tool set is available for Main Agent registration
- **WHEN** the Main Agent service constructs its tool list
- **THEN** the backend exposes tools named `update_plan`, `request_clarification`, `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_parallel_workers`, `read_artifact`, `run_quality_gate`, `write_final_report`, and `finish_task`

#### Scenario: Tools can be invoked without a live provider
- **WHEN** a developer invokes an agent tool directly from unit tests or a local development script
- **THEN** the tool executes through the same runtime service path used by the provider-facing tool-loop wrapper

### Requirement: Quality Gate and finish tools enforce final delivery policy
The backend SHALL provide tools for running Quality Gate and finishing tasks through existing runtime services and Scheduler Guard policy.

#### Scenario: Quality Gate tool persists audit trail
- **WHEN** `run_quality_gate` is invoked for a persisted task
- **THEN** the tool runs the Quality Gate service
- **AND** returns the aggregate assessment status, blocking flag, failed gate names, and gate report artifact reference

#### Scenario: Finish succeeded requires passing Quality Gate and final report
- **WHEN** `finish_task` is invoked with final status `succeeded`
- **THEN** the tool verifies that Scheduler Guard accepts successful completion
- **AND** verifies that a durable final report artifact has been written
- **AND** rejects the finish action if either condition fails

#### Scenario: Finish succeeded marks terminal task
- **WHEN** `finish_task` is invoked with final status `succeeded` for a task that passes Scheduler Guard completion validation and report durability validation
- **THEN** the tool persists task status `succeeded`
- **AND** sets phase `completed`
- **AND** sets `completed_at`
- **AND** emits a `task.succeeded` event after `main_agent.completed`

### Requirement: Main Agent orchestration finalization is report-first
The backend SHALL direct Main Agent orchestration to create final report artifacts before relying on a terminal `finish_task` tool call as the final mutation.

#### Scenario: Report tool replaces structured final output
- **WHEN** Quality Gate has passed and no blocking failures remain
- **THEN** orchestration instructions tell the model to call `write_final_report`
- **AND** then call `finish_task` with the intended final status
- **AND** Runtime does not require a final `MainAgentEpisodeOutput` from the model

#### Scenario: Direct finish tool remains guarded
- **WHEN** `finish_task` is invoked directly outside the expected report-first sequence
- **THEN** the tool still enforces Scheduler Guard finalization policy and report durability before mutating terminal task status

## REMOVED Requirements

### Requirement: Worker tools dispatch only validated classified tasks
**Reason**: The new tool-loop path no longer requires a prior structured classification object. Worker tools still require a valid runnable task state and Scheduler Guard approval.
**Migration**: Replace classification-only preconditions with guarded runnable-state validation, plan/clarification tools, and finalization invariants.
