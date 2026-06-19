## MODIFIED Requirements

### Requirement: Backend eval suite runs deterministic mock evals by default
The backend SHALL run the default eval suite deterministically without live model calls, external network calls, or real MCP workers.

#### Scenario: Default eval does not require provider secrets
- **WHEN** the default backend eval command runs
- **THEN** it does not require `OPENAI_API_KEY`, Main Agent provider credentials, `DEEPSEEK_API_KEY`, real provider credentials, or a real MCP server
- **AND** it uses scripted Main Agent tool-loop steps and mock worker scenarios

#### Scenario: Eval starts from normal task creation
- **WHEN** a deterministic eval case starts
- **THEN** it creates the task through the Task API path or the same TaskService path used by the Task API
- **AND** the raw user request artifact and `task.created` event exist before Runtime execution is asserted

#### Scenario: Eval executes through Runtime boundary
- **WHEN** a deterministic eval case runs
- **THEN** it invokes Runtime through the existing Runtime service boundary
- **AND** it exercises Main Agent service behavior, Main Agent public progress events, Main Agent tool calls, Scheduler Guard, mock MCP adapter, WorkerResult handling, Quality Gate, final report generation, event persistence, artifact persistence, and worker job persistence through normal code paths

### Requirement: Backend eval suite asserts stable Router invariants
The backend SHALL evaluate each case by inspecting persisted Router state and reusable named invariants.

#### Scenario: Eval audits persisted task outputs
- **WHEN** an eval case finishes or pauses
- **THEN** the harness loads persisted task state, worker jobs, artifacts, user-visible events, gate results, final report content, and Main Agent replay log content for assertions
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

#### Scenario: Main Agent model request does not require response_format
- **WHEN** deterministic evals exercise the provider runner boundary with a fake Chat Completions client
- **THEN** the captured Main Agent model request does not include `response_format`
- **AND** includes tool definitions for the tool-loop runner
