## ADDED Requirements

### Requirement: Router mock E2E tests verify final report contract
The mock E2E suite SHALL verify that terminal Runtime/Main Agent delivery scenarios persist final reports with stable content and artifact references.

#### Scenario: Successful development report references delivered artifacts
- **WHEN** the simple development mock E2E scenario completes with final task status `succeeded`
- **THEN** the scenario reads the `FINAL_REPORT` artifact through the Artifact Store or artifact API
- **AND** verifies the report content identifies the user goal, final status, task type, difficulty, final PLC code artifact ID, test report artifact ID, gate report artifact ID, and no unresolved blocking items
- **AND** verifies the report content does not embed full PLC code or full test report content

#### Scenario: Repair success report references repair evidence
- **WHEN** a mock E2E repair scenario completes with final task status `succeeded` after at least one repair round
- **THEN** the final report references the latest PLC code artifact, latest test or formal report artifact, latest patch artifact, latest repair summary artifact, and resolved failure evidence
- **AND** the report records the repair round count from persisted task state

#### Scenario: Repair exhaustion report explains partial failure
- **WHEN** the repair budget exhaustion mock E2E scenario completes with final task status `partial_failed`
- **THEN** the final report references the available PLC code, validation report, gate report, and repair artifacts
- **AND** the report records unresolved blocking failures and the exhausted repair round count
- **AND** the event log contains `main_agent.completed` before `task.partial_failed`

#### Scenario: Terminal reports are durable before terminal events
- **WHEN** any mock E2E scenario finalizes as `succeeded`, `partial_failed`, or `failed`
- **THEN** the final report artifact exists before the terminal task event is emitted
- **AND** the `main_agent.completed` event references the final report artifact ID
