## ADDED Requirements

### Requirement: Quality Gate assesses delivery readiness without side effects
The backend SHALL provide a deterministic Quality Gate assessment that evaluates a `TaskState` and reports gate outcomes without mutating task state, writing artifacts, appending events, or persisting gate result rows.

#### Scenario: Assessment returns all gate outcomes
- **WHEN** Quality Gate assesses any valid `TaskState`
- **THEN** the assessment contains outcomes for `requirements_gate`, `code_gate`, `test_gate`, `formal_gate`, `regression_gate`, and `final_gate`

#### Scenario: L1 QA task can pass without test evidence
- **WHEN** Quality Gate assesses a `qa` task with difficulty `L1`, no open required clarification, no blocking failure, `gates.test_required` equal to false, and `gates.formal_required` equal to false
- **THEN** the assessment passes without requiring `latest_test_report` or `latest_formal_report`

#### Scenario: L2 development task without test report fails
- **WHEN** Quality Gate assesses a `new_plc_development` task with difficulty `L2` or `gates.test_required` equal to true and no passing latest test evidence
- **THEN** `test_gate` fails with `blocking` equal to true
- **AND** the aggregate assessment status is `failed`

#### Scenario: Safety-critical task without formal report fails
- **WHEN** Quality Gate assesses a task with difficulty `L3` or higher, `gates.formal_required` equal to true, or safety-critical difficulty signals requiring formal verification and no passing latest formal evidence
- **THEN** `formal_gate` fails with `blocking` equal to true
- **AND** the aggregate assessment status is `failed`

#### Scenario: Open blocking failure fails final delivery
- **WHEN** Quality Gate assesses a task with `gates.has_blocking_failure` equal to true or an open failure whose severity is `blocking`
- **THEN** `final_gate` fails with `blocking` equal to true
- **AND** the aggregate assessment status is `failed`

#### Scenario: Pending regression fails final delivery
- **WHEN** Quality Gate assesses a task with `gates.regression_required` equal to true or `gates.formal_regression_required` equal to true
- **THEN** `regression_gate` fails with `blocking` equal to true
- **AND** the aggregate assessment status is `failed`

### Requirement: Quality Gate persists audit artifacts and gate result records
The backend SHALL provide a Quality Gate service method that runs the assessment for a persisted task and records the outcome through existing artifact, gate result, event, and task state persistence boundaries.

#### Scenario: Passing gate writes report and success marker
- **WHEN** the Quality Gate service runs for a persisted task whose assessment passes
- **THEN** the service writes a `gate_report` artifact for the task
- **AND** persists gate result records for the evaluated gates
- **AND** updates `TaskState.current_artifacts.latest_gate_report` to the new artifact reference
- **AND** updates `TaskState.gates.can_finish_as_success` to true

#### Scenario: Failing gate writes report and clears success marker
- **WHEN** the Quality Gate service runs for a persisted task whose assessment fails
- **THEN** the service writes a `gate_report` artifact for the task
- **AND** persists gate result records identifying the failed blocking gates
- **AND** updates `TaskState.current_artifacts.latest_gate_report` to the new artifact reference
- **AND** updates `TaskState.gates.can_finish_as_success` to false

#### Scenario: Gate lifecycle events are emitted
- **WHEN** the Quality Gate service runs for a persisted task
- **THEN** the event log contains a `gate.started` event followed by `gate.passed` when the aggregate assessment passes or `gate.failed` when the aggregate assessment fails
- **AND** the terminal gate event correlates to the written `gate_report` artifact

### Requirement: Quality Gate development runner verifies fixture states
The repository SHALL provide a local development script that runs Quality Gate against fixture task states for manual verification.

#### Scenario: Fixture with missing formal evidence fails
- **WHEN** a developer runs the Quality Gate development script with a fixture representing an L3 task that lacks passing formal evidence
- **THEN** the script prints an aggregate status of `failed`
- **AND** the printed result identifies `formal_gate` as a blocking failure
