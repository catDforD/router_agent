## ADDED Requirements

### Requirement: Task trace summary is available
The backend SHALL expose a read-only task trace summary that reconstructs a task's execution graph from persisted Router state without embedding large artifact or log content.

#### Scenario: Existing task trace summary is returned
- **WHEN** a client requests the trace summary for an existing task
- **THEN** the response includes the task ID, `openai_trace_id`, all Main Agent run IDs, the latest Main Agent run ID, worker job summaries, artifact summaries, gate result summaries, and correlated event summaries
- **AND** the response contains IDs and compact metadata rather than full artifact content, full logs, full PLC code, full reports, raw model outputs, or hidden reasoning

#### Scenario: Missing task trace summary is rejected
- **WHEN** a client requests the trace summary for a task ID that does not exist
- **THEN** the backend returns a not-found response
- **AND** no trace, event, worker job, artifact, or task mutation is created

### Requirement: Main Agent runs are trace-mapped
The backend SHALL map each persisted Main Agent run for a task to the Router events and artifacts produced by that run.

#### Scenario: Started Main Agent run appears in trace summary
- **WHEN** a Main Agent episode starts for a task
- **THEN** `TaskState.trace.main_agent_run_ids` includes the new run ID
- **AND** the trace summary includes a Main Agent run entry with the run ID, `openai_trace_id`, start event ID, start event sequence, and start timestamp

#### Scenario: Completed Main Agent run links report artifacts
- **WHEN** a Main Agent episode completes after writing a final report and replay log artifact
- **THEN** the trace summary links the run ID to the `main_agent.completed` event
- **AND** links the run ID to the final report artifact ID
- **AND** includes the replay log artifact ID only as bounded metadata without inlining replay log content

#### Scenario: Main Agent error remains traceable
- **WHEN** a Main Agent episode records an observable error event
- **THEN** the trace summary links the error event to the latest Main Agent run ID and `openai_trace_id`
- **AND** the task is not represented as successfully completed because of that error

### Requirement: Worker, MCP, and artifact IDs are trace-mapped
The backend SHALL preserve the relationship between a Main Agent run, worker dispatch, MCP request, worker result, and produced artifacts.

#### Scenario: Worker events carry inherited trace context
- **WHEN** a Main Agent tool dispatches a worker using a `WorkerInput` with trace context
- **THEN** the persisted `worker.started` event correlation includes the worker job ID, `openai_trace_id`, and Main Agent run ID
- **AND** terminal worker events for the same job include the same trace context when available

#### Scenario: Real MCP request ID appears in trace map
- **WHEN** a real or hybrid MCP worker dispatch assigns an `mcp_request_id`
- **THEN** the worker input and worker result trace contexts include that MCP request ID
- **AND** worker lifecycle events and the trace summary include the same MCP request ID for the worker job

#### Scenario: Produced artifacts are linked to worker job
- **WHEN** a worker produces artifacts that Router persists through the Artifact Store
- **THEN** each produced artifact summary in the trace summary includes the artifact ID, artifact type, version, visibility, summary, creator metadata, and derived worker job ID
- **AND** the worker job summary includes the produced artifact IDs

### Requirement: Gate and task lifecycle events are trace-correlated
The backend SHALL correlate Quality Gate and task lifecycle events with the task's current trace when the task trace is known.

#### Scenario: Quality Gate events include task trace
- **WHEN** the Quality Gate runs for a task whose trace contains an `openai_trace_id` or latest Main Agent run ID
- **THEN** `gate.started`, `gate.passed`, and `gate.failed` events include those IDs in event correlation when available
- **AND** the trace summary links gate result rows and gate report artifacts to the same task trace

#### Scenario: Cancellation event includes task trace
- **WHEN** a task is cancelled after a Main Agent run has started
- **THEN** the `task.cancelled` event includes the task's `openai_trace_id` and latest Main Agent run ID in event correlation
- **AND** the trace summary shows cancellation as the terminal task event for that trace

### Requirement: Router trace mapping works without external SDK trace export
The backend SHALL preserve Router-internal trace mapping even when external OpenAI SDK trace export is disabled, unsupported, or not configured.

#### Scenario: External tracing disabled still produces trace map
- **WHEN** a Main Agent episode runs with external SDK tracing disabled
- **THEN** `TaskState.trace.openai_trace_id` and `latest_main_agent_run_id` are still persisted
- **AND** worker inputs inherit the same Router trace context
- **AND** the trace summary can map Main Agent events, worker jobs, MCP request IDs, artifacts, gate results, and terminal task events by task ID

### Requirement: Trace summary is deterministic and bounded
The backend SHALL make trace summary output deterministic, compact, and suitable for frontend timeline or developer debugging views.

#### Scenario: Trace summary ordering is stable
- **WHEN** a trace summary contains events, worker jobs, artifacts, and gate results
- **THEN** event summaries are ordered by Router event sequence
- **AND** worker job, artifact, and gate result summaries are ordered by their persisted timestamps and stable IDs when timestamps tie

#### Scenario: Trace summary excludes large content
- **WHEN** trace-linked artifacts contain PLC code, test reports, formal reports, replay logs, patches, counterexamples, or other large content
- **THEN** the trace summary includes only metadata and references for those artifacts
- **AND** clients must use the artifact API to read permitted artifact content separately
