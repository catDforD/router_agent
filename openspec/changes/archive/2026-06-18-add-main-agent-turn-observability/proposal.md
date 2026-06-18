## Why

Main Agent episodes currently leave a gap between persisted runtime events and the model-driven orchestration loop: worker jobs, artifacts, and gates are visible, but per-turn decisions and the final Main Agent episode report are not durable or streamable. This makes successful runs hard to explain in the frontend and creates a failure mode where a task can become terminal without a persisted Main Agent report.

## What Changes

- Add durable Main Agent turn observability for orchestration episodes, including public decision summaries, tool selections, tool results, and turn indexes.
- Use Router events as the frontend streaming surface for turn progress, while storing larger replay data in a `MAIN_AGENT_LOG` artifact.
- Persist the final orchestration output as a user-visible `FINAL_REPORT` artifact and emit a `main_agent.completed` event that references it.
- Adopt the safer finalization flow where Runtime validates and persists the final `MainAgentEpisodeOutput` before marking the task terminal.
- Add optional tool-call rationale summary fields so model decisions can be captured atomically with tool calls without exposing hidden chain-of-thought.
- Add streaming runner support for official OpenAI Agents SDK runs by translating SDK stream/hook events into stable Router events.

## Capabilities

### New Capabilities
- `main-agent-turn-observability`: Captures and streams Main Agent orchestration turn decisions, tool calls, tool results, final reports, and replay logs.

### Modified Capabilities
- `event-streaming-api`: Frontend-visible SSE SHALL include Main Agent turn, tool, and completion events in persisted sequence order.
- `local-artifact-store`: Artifact storage SHALL persist Main Agent final reports and larger turn replay logs using existing artifact types.
- `main-agent-function-tools`: Main Agent tool calls SHALL support bounded public rationale summaries and finalization SHALL be coordinated by Runtime.
- `router-v1-schema-contract`: Router v1 event type vocabulary SHALL include Main Agent turn/tool/completion observability events without adding a new artifact type.

## Impact

- Affected code:
  - `backend/app/agents/main_agent.py`
  - `backend/app/agents/tools.py`
  - `backend/app/agents/instructions.py`
  - `backend/app/agents/output_schema.py`
  - `backend/app/services/runtime_service.py`
  - `backend/app/services/event_service.py`
  - `backend/app/services/artifact_store.py`
  - `backend/app/models/router_schema.py`
  - `schema/` JSON Schema files and `schema/ts/router_contract.d.ts`
  - Main Agent, runtime, event streaming, artifact, and schema tests
- Public HTTP routes remain unchanged, but existing task event streams expose additional user-visible event types.
- No new database tables are required; events and artifacts use existing persistence.
- No new artifact type is required; final reports use `FINAL_REPORT` and replay logs use `MAIN_AGENT_LOG`.
