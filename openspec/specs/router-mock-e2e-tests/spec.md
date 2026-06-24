# router-mock-e2e-tests Specification

## Purpose
TBD - created by archiving change add-router-mock-e2e-tests. Update Purpose after archive.
## Requirements
### Requirement: Router mock E2E tests execute deterministic scenarios
The repository SHALL provide automated mock end-to-end tests that execute Router runtime scenarios deterministically without live OpenAI calls or real MCP workers.

#### Scenario: E2E scenario uses fake Main Agent runner
- **WHEN** the mock E2E suite runs any Router scenario
- **THEN** the scenario uses a fake or scripted Main Agent runner that drives tool-loop orchestration through deterministic tool/service calls
- **AND** the scenario does not require `OPENAI_API_KEY`
- **AND** the scenario does not call a real MCP server

#### Scenario: E2E scenario starts from task creation
- **WHEN** a mock E2E scenario begins
- **THEN** it creates a task through the Task API or the same TaskService path used by the Task API
- **AND** it verifies the raw user request artifact and `task.created` event exist before Runtime execution is asserted

#### Scenario: E2E scenario drives Runtime through service boundary
- **WHEN** a mock E2E scenario runs Runtime execution
- **THEN** it invokes `RuntimeService.start_task` or an equivalent runtime service boundary for the created task
- **AND** it exercises Main Agent tools, mock MCP adapter, WorkerResult handler, Quality Gate, and final task status persistence through normal runtime paths

### Requirement: Router mock E2E tests cover step-17 scenario matrix
The mock E2E suite SHALL cover the scenario matrix from `docs/backend.md` step 17.

#### Scenario: Simple development succeeds
- **WHEN** the scenario models a simple development request with mock development and tests passing
- **THEN** the final task status is `succeeded`
- **AND** worker jobs include `plc-dev` followed by `plc-test`
- **AND** artifacts include PLC code, test report, gate report, final report, and main agent log
- **AND** the event log contains task creation, Main Agent start, worker lifecycle, artifact creation, gate pass, Main Agent completion, and task success events in valid order

#### Scenario: Test failure repairs and succeeds
- **WHEN** the scenario models test failure followed by successful repair and passing regression
- **THEN** the final task status is `succeeded`
- **AND** worker jobs include `plc-dev`, initial `plc-test`, `plc-repair`, and regression `plc-test`
- **AND** `TaskState.runtime_limits.repair_rounds` equals `1`
- **AND** the original blocking test failure is resolved
- **AND** `TaskState.gates.regression_required` is false before terminal success

#### Scenario: Formal failure repairs and succeeds
- **WHEN** the scenario models a safety-critical request whose formal verification fails before repair and passes after repair
- **THEN** the final task status is `succeeded`
- **AND** worker jobs include `plc-dev`, `plc-test`, initial `plc-formal`, `plc-repair`, regression `plc-test`, and regression `plc-formal`
- **AND** `TaskState.runtime_limits.repair_rounds` equals `1`
- **AND** the original blocking formal failure is resolved
- **AND** `TaskState.gates.formal_regression_required` is false before terminal success

#### Scenario: Requirement clarification pauses execution
- **WHEN** the scenario models an incomplete request that requires clarification
- **THEN** the final task status is `waiting_user`
- **AND** the task phase is `clarifying`
- **AND** at least one open required clarification question is persisted
- **AND** no worker job is created
- **AND** the event log contains clarification-requested and waiting-user events

#### Scenario: Repair budget exhaustion ends partially failed
- **WHEN** the scenario models repeated validation failure through the maximum configured repair rounds
- **THEN** the final task status is `partial_failed`
- **AND** `TaskState.runtime_limits.repair_rounds` equals `TaskState.runtime_limits.max_repair_rounds`
- **AND** no fourth repair worker job is created
- **AND** at least one blocking failure remains open
- **AND** Quality Gate records a blocking failed assessment before partial failure is finalized

### Requirement: Router mock E2E tests verify persisted audit trail
Each mock E2E scenario SHALL verify Router replay surfaces from persisted state rather than relying only on returned in-memory outputs.

#### Scenario: Worker job audit is persisted
- **WHEN** a mock E2E scenario dispatches workers
- **THEN** each expected worker job row exists with the expected worker type, terminal status, persisted input, and persisted result

#### Scenario: Artifact audit is persisted
- **WHEN** a mock E2E scenario produces worker, gate, or Main Agent artifacts
- **THEN** artifact rows exist for the expected artifact types
- **AND** important versioned artifacts, such as repaired PLC code and regression reports, retain distinct versions instead of overwriting prior artifacts
- **AND** `TaskState.current_artifacts` points at the latest relevant artifact refs

#### Scenario: Event audit is persisted in order
- **WHEN** a mock E2E scenario completes or pauses
- **THEN** user-visible events for the task have monotonically increasing sequence numbers
- **AND** the required event subsequence for that scenario appears in valid chronological order

#### Scenario: Gate audit is persisted
- **WHEN** a mock E2E scenario runs Quality Gate
- **THEN** gate result rows exist for the evaluated gate types
- **AND** the latest gate report artifact is recorded on `TaskState.current_artifacts`
- **AND** the terminal gate event correlates with the gate report artifact

### Requirement: Router mock E2E local smoke script is available
The repository SHALL provide a local development script for running one deterministic mock E2E scenario outside pytest.

#### Scenario: Developer runs mock E2E script
- **WHEN** a developer runs the mock E2E script with a supported scenario name
- **THEN** the script creates or selects a task, runs the deterministic mock Router flow, and prints the task ID, final status, worker job summary, artifact summary, event summary, and gate summary

#### Scenario: Script rejects unsupported scenario
- **WHEN** a developer runs the mock E2E script with an unsupported scenario name
- **THEN** the script exits with a non-zero status or argparse validation error before creating worker jobs

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
