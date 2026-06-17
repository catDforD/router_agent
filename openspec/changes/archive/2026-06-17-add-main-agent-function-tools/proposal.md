## Why

The backend now has the core runtime boundaries needed to dispatch PLC workers, persist worker artifacts, apply `WorkerResult` state updates, and run Quality Gate checks, but Main Agent has no safe function tools for using those capabilities. This change creates the tool boundary before wiring the OpenAI Agents SDK runner so each tool can be tested and debugged without a live Main Agent.

## What Changes

- Add Main Agent function tools for `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_parallel_workers`, `read_artifact`, `run_quality_gate`, and `finish_task`.
- Add a deterministic worker input builder that selects current task artifacts, constructs validated `WorkerInput` payloads, and centralizes worker mode / expected output mapping.
- Route worker tools through Scheduler Guard, the MCP adapter, and WorkerResult Handler so Runtime policy and state projection remain authoritative.
- Return compact tool summaries to Main Agent, including status, summary, artifact refs, failure summaries, gate state, and next recommended action, without returning full code or full logs by default.
- Track active worker jobs and worker call counters around tool dispatch so concurrency and worker budget checks remain meaningful.
- Keep intake classification out of this change: these tools operate on already-classified running tasks, while classification remains part of the later Main Agent service/runtime episode.
- Add tests and a development script so each tool can be invoked without starting a Main Agent run.

## Capabilities

### New Capabilities
- `main-agent-function-tools`: Defines the backend function tool contract used by Main Agent to call PLC workers, read artifacts, run Quality Gate, and finish tasks safely.

### Modified Capabilities

None.

## Impact

- Affected code:
  - `backend/app/agents/tools.py`
  - `backend/app/workers/worker_input_builder.py`
  - `backend/app/services/runtime_service.py` only if a small shared state-transition helper is needed
  - `backend/app/tests/unit/test_agent_tools.py`
  - `scripts/dev_call_agent_tool.py`
- Existing runtime services reused:
  - `SchedulerGuard`
  - `McpAdapter`
  - `WorkerResultHandler`
  - `ArtifactStore`
  - `QualityGateService`
  - task, artifact, event, and worker job repositories
- Dependency impact:
  - May add `openai-agents` for SDK tool wrappers, but the core tool service should remain testable without invoking an OpenAI model.
- API impact:
  - No public HTTP API changes are required.
- Contract impact:
  - No Router v1 schema change is expected.
