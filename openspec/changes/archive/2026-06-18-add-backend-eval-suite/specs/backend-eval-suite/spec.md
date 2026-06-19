## ADDED Requirements

### Requirement: Backend eval suite defines a fixed PLC task corpus
The backend SHALL provide a fixed PLC task evaluation corpus that is stored in the repository and can be reviewed independently from the pytest harness.

#### Scenario: Eval corpus contains representative cases
- **WHEN** the backend eval suite loads the PLC task corpus
- **THEN** the corpus contains at least 15 cases
- **AND** each case has a stable `id`, user `message`, expected final status policy, and expected Router behavior assertions

#### Scenario: Eval corpus covers required PLC workflows
- **WHEN** the backend eval corpus is reviewed
- **THEN** it includes cases for simple QA or explanation, ordinary PLC development, emergency stop logic, fault latch and reset logic, motor interlock logic, auto/manual mode switching, conveyor sequence control, timer logic, counter logic, clarification, existing-code modification, test-failure repair, formal-counterexample repair, repair budget exhaustion, and worker timeout or error behavior

#### Scenario: Eval corpus validates contract values
- **WHEN** the eval harness parses a case
- **THEN** task types, difficulty levels, worker types, artifact types, event types, and final statuses used by the case are validated against Router contract values before the case executes

### Requirement: Backend eval suite runs deterministic mock evals by default
The backend SHALL run the default eval suite deterministically without live model calls, external network calls, or real MCP workers.

#### Scenario: Default eval does not require provider secrets
- **WHEN** the default backend eval command runs
- **THEN** it does not require `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, real provider credentials, or a real MCP server
- **AND** it uses scripted Main Agent outputs and mock worker scenarios

#### Scenario: Eval starts from normal task creation
- **WHEN** a deterministic eval case starts
- **THEN** it creates the task through the Task API path or the same TaskService path used by the Task API
- **AND** the raw user request artifact and `task.created` event exist before Runtime execution is asserted

#### Scenario: Eval executes through Runtime boundary
- **WHEN** a deterministic eval case runs
- **THEN** it invokes Runtime through the existing Runtime service boundary
- **AND** it exercises Main Agent service behavior, Main Agent tool calls, Scheduler Guard, mock MCP adapter, WorkerResult handling, Quality Gate, final report generation, event persistence, artifact persistence, and worker job persistence through normal code paths

### Requirement: Backend eval suite asserts stable Router invariants
The backend SHALL evaluate each case by inspecting persisted Router state and reusable named invariants.

#### Scenario: Eval audits persisted task outputs
- **WHEN** an eval case finishes or pauses
- **THEN** the harness loads persisted task state, worker jobs, artifacts, user-visible events, gate results, and final report content for assertions
- **AND** it does not rely only on returned in-memory Runtime output

#### Scenario: L3 tasks cannot skip formal verification
- **WHEN** an eval case expects difficulty `L3` or formal verification to be required
- **THEN** a successful final status is accepted only if a `plc-formal` worker job ran and latest formal evidence passed before terminal success

#### Scenario: Repair requires regression validation
- **WHEN** an eval case includes a repair worker job after a test failure
- **THEN** terminal success is accepted only if a later regression `plc-test` job ran and regression-required state was cleared before terminal success

#### Scenario: Formal repair requires formal regression
- **WHEN** an eval case includes repair after a formal failure
- **THEN** terminal success is accepted only if a later `plc-formal` job ran and formal-regression-required state was cleared before terminal success

#### Scenario: Clarification creates no worker jobs
- **WHEN** an eval case expects a required clarification
- **THEN** the final task status is `waiting_user`
- **AND** at least one open required clarification question is persisted
- **AND** no worker job, worker event, or worker artifact is created

#### Scenario: Success requires Quality Gate and final report
- **WHEN** an eval case finishes with final status `succeeded`
- **THEN** a passing Quality Gate result exists before terminal success
- **AND** a final report artifact exists
- **AND** `main_agent.completed` is emitted before the terminal task event

### Requirement: Backend eval suite writes a compact eval report
The backend SHALL produce a compact Markdown eval report for completed eval runs.

#### Scenario: Eval report summarizes every case
- **WHEN** the eval suite completes
- **THEN** the eval report lists each case ID, result, expected final status policy, actual final status, worker sequence, artifact summary, and invariant results

#### Scenario: Eval report includes failure diagnostics
- **WHEN** an eval case fails
- **THEN** the eval report includes a bounded failure reason and enough task identifiers to inspect persisted state

#### Scenario: Eval report excludes large artifacts
- **WHEN** the eval report is written
- **THEN** it does not embed full PLC code, full test reports, full formal reports, counterexamples, patches, worker logs, or Main Agent replay logs

### Requirement: Backend eval suite supports opt-in live provider evaluation
The backend SHALL keep live model or provider-backed evaluation separate from the default deterministic eval path.

#### Scenario: Live eval is skipped unless explicitly enabled
- **WHEN** the eval suite runs without the live eval opt-in flag or environment variable
- **THEN** live provider cases are skipped or not selected
- **AND** the eval suite remains fully offline and deterministic

#### Scenario: Live eval uses broad policy assertions
- **WHEN** live provider eval is explicitly enabled
- **THEN** it may reuse the fixed task corpus
- **AND** it asserts broad required and forbidden Router behavior, policy invariants, and persisted audit surfaces instead of requiring an exact scripted tool sequence
