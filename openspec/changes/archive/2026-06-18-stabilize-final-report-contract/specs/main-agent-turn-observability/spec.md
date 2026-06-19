## MODIFIED Requirements

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
