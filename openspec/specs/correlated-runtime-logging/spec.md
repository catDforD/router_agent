# correlated-runtime-logging Specification

## Purpose
TBD - created by archiving change add-router-trace-mapping-and-correlated-logging. Update Purpose after archive.
## Requirements
### Requirement: Runtime logs include trace context
The backend SHALL emit runtime logs with structured correlation context at high-value execution boundaries without relying on logs as the durable audit source.

#### Scenario: Main Agent episode logs include trace identifiers
- **WHEN** a Main Agent episode starts, completes, pauses, or fails
- **THEN** the emitted runtime log record includes the task ID, `openai_trace_id` when available, and Main Agent run ID when available
- **AND** the same execution step remains represented by persisted Router task state, events, artifacts, or worker job rows

#### Scenario: Worker dispatch logs include worker identifiers
- **WHEN** Runtime dispatches a worker or receives a terminal worker result
- **THEN** the emitted runtime log record includes the task ID, worker job ID, worker type, `openai_trace_id` when available, Main Agent run ID when available, and MCP request ID when available
- **AND** the log record does not replace the persisted worker job or Router event audit trail

#### Scenario: MCP call logs include request identifier
- **WHEN** a real MCP worker request is sent, times out, or fails
- **THEN** the emitted runtime log record includes the task ID, worker job ID, MCP tool name, and MCP request ID when assigned
- **AND** the log record does not include API keys, tokens, secret URLs, full request bodies, full responses, or artifact content

### Requirement: Runtime logging redacts secrets and large content
The backend SHALL prevent runtime logging helpers from emitting secret values or large task artifacts.

#### Scenario: Secret values are redacted
- **WHEN** runtime logging receives diagnostic context containing keys such as API key, token, secret, password, authorization, or database URL credentials
- **THEN** the emitted log output redacts those values
- **AND** tests prove configured provider keys and database passwords are not present in captured logs

#### Scenario: Large content is omitted
- **WHEN** runtime logging receives artifact content, PLC code, test logs, formal reports, replay logs, raw model output, or raw MCP response content
- **THEN** the emitted log output omits the content body
- **AND** may include bounded metadata such as artifact ID, content hash, content size, event sequence, status, or error code

### Requirement: Trace summary failures are diagnosable
The backend SHALL log trace-summary projection failures with enough context to diagnose the issue without leaking protected content.

#### Scenario: Trace summary projection fails
- **WHEN** the backend cannot build a trace summary for an existing task because of inconsistent persisted data or repository failure
- **THEN** the backend emits an error log with the task ID and non-secret exception classification
- **AND** the API returns an error response without creating task, event, artifact, worker job, or gate result mutations

### Requirement: Logs remain secondary to persisted Router audit data
The backend SHALL keep Router events, artifacts, task state, worker jobs, and gate results as the authoritative execution audit data.

#### Scenario: Persisted audit exists without log access
- **WHEN** process logs are unavailable, rotated, sampled, or disabled below the relevant log level
- **THEN** a client can still use persisted Router APIs and the trace summary to inspect task execution state
- **AND** no required trace mapping behavior depends on reading process logs
