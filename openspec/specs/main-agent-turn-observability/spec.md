# main-agent-turn-observability Specification

## Purpose
TBD - created by archiving change add-main-agent-turn-observability. Update Purpose after archive.
## Requirements
### Requirement: Main Agent orchestration turns are observable
The backend SHALL capture user-visible Main Agent orchestration turn progress as durable Router events without exposing hidden model chain-of-thought.

#### Scenario: Turn start is recorded
- **WHEN** a Main Agent orchestration run invokes the model for a new turn
- **THEN** the backend emits a user-visible `main_agent.turn_started` event with the task ID, main agent run ID, turn index, and phase

#### Scenario: Tool decision is recorded
- **WHEN** the orchestration model selects a Main Agent tool
- **THEN** the backend emits a user-visible `main_agent.tool_called` event with the turn index, tool name, bounded public rationale summary, sanitized tool arguments, and referenced input artifact IDs
- **AND** the event does not include hidden chain-of-thought or full artifact content

#### Scenario: Tool result is recorded
- **WHEN** a selected Main Agent tool returns a result to the model
- **THEN** the backend emits a user-visible `main_agent.tool_result` event with the turn index, tool name, result status, compact summary, produced artifact IDs, failure IDs, and worker job ID when available
- **AND** the event does not include full PLC code, full logs, full reports, patches, or counterexamples

### Requirement: Main Agent replay log is persisted
The backend SHALL persist a larger replay log for each orchestration episode as a `MAIN_AGENT_LOG` artifact.

#### Scenario: Replay log captures normalized entries
- **WHEN** a Main Agent orchestration episode completes, pauses, or fails after starting
- **THEN** the backend writes a `MAIN_AGENT_LOG` artifact containing normalized turn entries, public rationale summaries, tool selections, compact tool results, final output metadata, and error metadata when applicable

#### Scenario: Replay log excludes hidden reasoning
- **WHEN** the backend writes the `MAIN_AGENT_LOG` artifact
- **THEN** the artifact excludes hidden chain-of-thought and excludes full large artifact contents unless they were explicitly read through a bounded artifact read tool result

### Requirement: Final Main Agent report is durable before terminal success
The backend SHALL persist a user-visible `FINAL_REPORT` artifact before applying any intentional Main Agent/Runtime terminal delivery status of `succeeded`, `partial_failed`, or `failed`.

#### Scenario: Successful final output writes report before terminal status
- **WHEN** orchestration produces a valid `MainAgentEpisodeOutput` recommending `succeeded` and Scheduler Guard finalization policy passes
- **THEN** the backend writes a user-visible `FINAL_REPORT` artifact
- **AND** writes a `MAIN_AGENT_LOG` artifact
- **AND** emits a user-visible `main_agent.completed` event referencing those artifact IDs
- **AND** only then marks the task `succeeded` and emits `task.succeeded`

#### Scenario: Partial failure writes report before terminal status
- **WHEN** orchestration produces a valid `MainAgentEpisodeOutput` recommending `partial_failed` after Quality Gate records unresolved blocking or incomplete delivery evidence
- **THEN** the backend writes a user-visible `FINAL_REPORT` artifact that identifies unresolved blocking items and available delivery artifacts
- **AND** writes a `MAIN_AGENT_LOG` artifact
- **AND** emits `main_agent.completed` before marking the task `partial_failed`
- **AND** emits `task.partial_failed` after the report artifacts are durable

#### Scenario: Runtime terminal failure writes deterministic failure report
- **WHEN** Runtime intentionally terminalizes a non-terminal task as `failed` because an unrecoverable Main Agent control-plane failure prevents reliable continuation
- **THEN** the backend writes a user-visible `FINAL_REPORT` artifact that records the failure code, failure message, current artifact references, gate state, and unresolved items
- **AND** writes or links the available `MAIN_AGENT_LOG` artifact when replay entries exist
- **AND** emits the terminal `task.failed` event only after the failure report is durable

#### Scenario: Invalid final output does not mark success
- **WHEN** orchestration work has completed but the final `MainAgentEpisodeOutput` is missing, malformed, or fails validation
- **THEN** the backend emits an observable Main Agent error event
- **AND** does not mark the task `succeeded`
- **AND** preserves any completed worker artifacts and gate artifacts for inspection

#### Scenario: Final report contains compact delivery summary
- **WHEN** the backend writes the `FINAL_REPORT` artifact
- **THEN** the artifact content includes the task ID, main agent run ID, report version, final task status, user goal, task type, difficulty, summary, plan steps, decisions, delivery artifact references, validation summary, repair summary, assumptions, unresolved items, gate summary, trace references, and creation timestamp
- **AND** the artifact references large outputs by artifact ID, type, version, summary, URI, and content hash instead of embedding full content

#### Scenario: Report persistence failure leaves terminal status unapplied
- **WHEN** final report or replay log artifact persistence fails before a `succeeded`, `partial_failed`, or `failed` terminal delivery status is applied
- **THEN** Runtime records an observable error
- **AND** does not emit `main_agent.completed`
- **AND** does not emit the terminal task event for that status

### Requirement: Runtime owns orchestration terminal finalization
The backend SHALL make Runtime responsible for applying successful terminal task status after final output validation and report persistence.

#### Scenario: Model recommends final status
- **WHEN** the orchestration model completes after Quality Gate execution
- **THEN** the model returns a structured final output that recommends the final status
- **AND** Runtime validates the recommendation against current task state, Scheduler Guard finalization policy, and Quality Gate state before mutating terminal status

#### Scenario: Report persistence failure leaves task non-terminal
- **WHEN** final report or replay log artifact persistence fails
- **THEN** Runtime records an observable error
- **AND** does not mark the task `succeeded`

### Requirement: Streaming runner feeds Router observability
The backend SHALL use streaming-capable model execution for official OpenAI Agents SDK orchestration when Main Agent turn observability is enabled.

#### Scenario: Streaming events become Router events
- **WHEN** the official OpenAI Agents SDK runner emits semantic streaming events or lifecycle hook callbacks for an orchestration run
- **THEN** the backend translates relevant model turn, tool call, tool output, and final output signals into stable Router events and replay log entries

#### Scenario: Streaming implementation remains runner-neutral
- **WHEN** another Main Agent runner implementation is used
- **THEN** the runner can report equivalent normalized observability entries without exposing SDK-specific event shapes to the frontend API

