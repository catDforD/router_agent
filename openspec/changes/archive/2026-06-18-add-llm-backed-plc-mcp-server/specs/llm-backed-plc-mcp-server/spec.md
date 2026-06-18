## ADDED Requirements

### Requirement: Router can connect to a real PLC worker MCP server
The backend SHALL provide a real MCP client path for Router worker dispatch over streamable HTTP.

#### Scenario: MCP tool discovery lists PLC worker tools
- **WHEN** the configured PLC worker MCP server is reachable
- **THEN** the backend SHALL be able to list tools through the MCP client
- **AND** the listed tool names SHALL include `plc_dev.run`, `plc_test.run`, `plc_formal.run`, and `plc_repair.run`

#### Scenario: Real MCP mode dispatches a worker input
- **WHEN** `MCP_MODE` is `real` and a guarded Main Agent tool dispatches a valid Router v1 `WorkerInput`
- **THEN** `McpAdapter` SHALL call the matching MCP tool instead of the in-process mock worker
- **AND** the worker job, worker events, and returned tool result SHALL remain correlated with the original `worker_job_id`

#### Scenario: MCP request id is recorded
- **WHEN** `McpAdapter` dispatches a worker through the real MCP client
- **THEN** the worker input and worker result trace context SHALL include an `mcp_request_id`
- **AND** worker lifecycle events SHALL include that `mcp_request_id` in their correlation data

### Requirement: Local PLC worker MCP server simulates subagents with DeepSeek
The backend SHALL provide a local MCP server whose PLC worker tools simulate subagent behavior by calling DeepSeek through an OpenAI-compatible chat-completions API.

#### Scenario: DeepSeek settings are worker-server scoped
- **WHEN** the local PLC worker MCP server starts
- **THEN** it SHALL read worker simulation provider settings from `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`
- **AND** it SHALL NOT require Main Agent `OPENAI_API_KEY` settings for worker simulation

#### Scenario: Main Agent provider remains separate
- **WHEN** Router runs a Main Agent episode that calls PLC worker tools
- **THEN** Main Agent execution SHALL continue using the existing Main Agent/OpenAI configuration
- **AND** only the MCP worker server's simulated subagent calls SHALL use DeepSeek configuration

#### Scenario: Worker tools accept Router worker inputs
- **WHEN** any local PLC worker MCP tool is called
- **THEN** the tool SHALL validate the supplied Router v1 `WorkerInput`
- **AND** it SHALL reject inputs whose `worker_type` does not match the called tool name

### Requirement: MCP worker responses are persisted as Router artifacts
The backend SHALL treat MCP worker output as draft artifact writes and SHALL persist produced content through Router's Artifact Store before constructing a canonical Router v1 `WorkerResult`.

#### Scenario: Draft artifacts become produced artifact refs
- **WHEN** an MCP worker returns artifact write drafts
- **THEN** `McpAdapter` SHALL write each draft through the existing Artifact Store
- **AND** the final `WorkerResult.produced_artifacts` SHALL contain persisted `ArtifactRef` entries with Router-generated artifact IDs, URIs, and content hashes

#### Scenario: Invalid MCP worker output is rejected
- **WHEN** an MCP worker response cannot be validated as the expected draft output shape
- **THEN** `McpAdapter` SHALL complete the worker job with an error status
- **AND** it SHALL return a standard Router v1 `WorkerResult` with `execution_status: "error"` and an appropriate schema-invalid error code

#### Scenario: Worker-specific required artifacts are enforced
- **WHEN** an MCP worker reports a passed outcome
- **THEN** `McpAdapter` SHALL verify that required artifact types for that worker are present before applying the result as successful
- **AND** missing required artifacts SHALL be normalized as a worker schema error

### Requirement: Real and hybrid worker routing is configurable
The backend SHALL allow each PLC worker to route independently to the real MCP server or the existing in-process mock worker.

#### Scenario: Mock mode remains default
- **WHEN** no MCP mode configuration is provided
- **THEN** Router SHALL continue using the existing mock worker path
- **AND** no MCP server URL or DeepSeek settings SHALL be required

#### Scenario: Hybrid mode routes individual workers
- **WHEN** per-worker mode settings such as `PLC_DEV_MODE`, `PLC_TEST_MODE`, `PLC_FORMAL_MODE`, or `PLC_REPAIR_MODE` are set to `mock` or `real`
- **THEN** `McpAdapter` SHALL route each worker according to its configured mode
- **AND** unconfigured worker modes SHALL fall back to the global MCP mode behavior

#### Scenario: Unsupported worker mode is rejected
- **WHEN** a worker mode setting has a value other than `mock` or `real`
- **THEN** backend settings validation SHALL reject the configuration before worker dispatch

### Requirement: LLM-backed plc-dev produces development artifacts
The local MCP `plc_dev.run` tool SHALL simulate PLC development and return draft artifacts sufficient for Router to create current code state.

#### Scenario: PLC development returns code and IO contract
- **WHEN** `plc_dev.run` receives a valid `plc-dev` `WorkerInput`
- **THEN** the final Router `WorkerResult` SHALL have `execution_status: "completed"`
- **AND** its outcome status SHALL be `passed` or `need_clarification`
- **AND** a passed result SHALL produce at least `plc_code` and `io_contract` artifacts with non-empty content

#### Scenario: PLC development updates task current code
- **WHEN** a passed `plc-dev` MCP result is handled by Router
- **THEN** `TaskState.current_artifacts.current_code` SHALL reference the produced `plc_code` artifact
- **AND** `TaskState.current_artifacts.current_io_contract` SHALL reference the produced `io_contract` artifact

### Requirement: LLM-backed plc-test produces test reports and failures
The local MCP `plc_test.run` tool SHALL simulate PLC testing from requirements and current code artifacts.

#### Scenario: PLC test pass records a test report
- **WHEN** `plc_test.run` receives valid requirements and PLC code input and reports a passing outcome
- **THEN** the final Router `WorkerResult` SHALL produce a readable `test_report` artifact
- **AND** WorkerResult metrics SHALL include test metrics when available

#### Scenario: PLC test failure records evidence
- **WHEN** `plc_test.run` reports a failed outcome
- **THEN** the final Router `WorkerResult` SHALL include at least one blocking test failure
- **AND** it SHALL produce a `test_report` artifact and evidence such as a `failing_trace` artifact
- **AND** Router result handling SHALL set `TaskState.gates.has_blocking_failure` to true

### Requirement: LLM-backed plc-formal produces formal reports and counterexamples
The local MCP `plc_formal.run` tool SHALL simulate formal verification from requirements, current code, and safety constraints.

#### Scenario: Formal pass records a formal report
- **WHEN** `plc_formal.run` reports a passing outcome
- **THEN** the final Router `WorkerResult` SHALL produce a readable `formal_report` artifact
- **AND** Router result handling SHALL set `TaskState.gates.latest_formal_passed` to true

#### Scenario: Formal failure records counterexample evidence
- **WHEN** `plc_formal.run` reports a failed outcome
- **THEN** the final Router `WorkerResult` SHALL include at least one blocking formal failure
- **AND** it SHALL produce a `formal_report` artifact and a `counterexample` artifact
- **AND** Router result handling SHALL make the counterexample available as repair input evidence

### Requirement: LLM-backed plc-repair produces patch and patched code
The local MCP `plc_repair.run` tool SHALL simulate PLC repair from current code and failure evidence.

#### Scenario: Repair without failure is still guard-rejected
- **WHEN** Main Agent attempts to call `plc-repair` without current failure evidence
- **THEN** Scheduler Guard SHALL reject the worker call before MCP dispatch
- **AND** no MCP worker job SHALL be created for the rejected repair

#### Scenario: Repair success updates current code
- **WHEN** `plc_repair.run` returns a passed repair result
- **THEN** the final Router `WorkerResult` SHALL produce `patch`, `plc_code`, and `repair_summary` artifacts
- **AND** the patched `plc_code` artifact version SHALL be newer than the input code version
- **AND** Router result handling SHALL update `TaskState.current_artifacts.current_code` to the patched code artifact

#### Scenario: Repair requires regression
- **WHEN** a passed `plc-repair` MCP result is handled by Router
- **THEN** `TaskState.runtime_limits.repair_rounds` SHALL increment
- **AND** `TaskState.gates.regression_required` SHALL be true

### Requirement: MCP failures are normalized and observable
The backend SHALL normalize MCP transport, timeout, provider, and draft validation failures into replayable Router worker results and events.

#### Scenario: MCP timeout is normalized
- **WHEN** a real MCP worker call exceeds `PLC_WORKER_TIMEOUT_SECONDS`
- **THEN** the worker job SHALL complete with timeout status
- **AND** the returned `WorkerResult` SHALL have `execution_status: "timeout"` and a retryable error
- **AND** the task event log SHALL contain a `worker.timeout` event

#### Scenario: MCP connection failure is normalized
- **WHEN** the configured MCP server cannot be reached during worker dispatch
- **THEN** the worker job SHALL complete with error status
- **AND** the returned `WorkerResult` SHALL have `execution_status: "error"`
- **AND** the task event log SHALL contain a `worker.error` event without leaking secret configuration values

#### Scenario: LLM provider error is normalized
- **WHEN** the MCP server cannot obtain a valid DeepSeek simulated worker response
- **THEN** the MCP worker response SHALL cause Router to return a standard error `WorkerResult`
- **AND** the error details SHALL be diagnostic enough to identify provider failure type without exposing API keys
