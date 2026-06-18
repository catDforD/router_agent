## Why

The backend can already execute guarded worker tools and mock worker chains, but there is no Main Agent service to classify created tasks, run an OpenAI Agents SDK episode, emit `main_agent.*` events, or drive the existing tools from a compact task state view.

This blocks `docs/backend.md` step 15: a newly created task remains `created/intake/unknown`, while the worker tools correctly reject unclassified tasks before any PLC worker dispatch.

## What Changes

- Add a Main Agent service that can run one deterministic, testable agent episode for a persisted task.
- Add a structured intake classification output and runtime application path so `created/intake/unknown` tasks become either `running/planning` or `waiting_user/clarifying` before worker orchestration.
- Add Main Agent instructions for compact artifact-oriented orchestration through existing function tools.
- Add structured final episode output for plan, decisions, artifact references, gate result, and terminal status.
- Emit observable `main_agent.started`, `main_agent.decision`, `main_agent.plan_updated`, `main_agent.clarification_requested`, and `main_agent.finalizing` events as appropriate.
- Persist base trace linkage on `TaskState.trace` so worker inputs inherit `openai_trace_id` and `main_agent_run_id`.
- Keep task creation synchronous and conservative; automatic background execution remains part of the later Runtime Loop change.

## Capabilities

### New Capabilities

- `main-agent-service`: Runs Main Agent intake and orchestration episodes over persisted Router tasks using compact state views, OpenAI Agents SDK tools, structured outputs, events, and trace linkage.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `backend/app/agents/main_agent.py`
  - `backend/app/agents/instructions.py`
  - `backend/app/agents/output_schema.py`
  - focused tests under `backend/app/tests/unit/` and `backend/app/tests/integration/`
- Existing runtime services reused:
  - `AgentToolService` and SDK tool wrappers
  - `TaskRepository`, `ArtifactStore`, `EventService`
  - `SchedulerGuard`, `QualityGateService`, `WorkerResultHandler`, and mock MCP adapter through existing tools
- Existing public HTTP APIs stay unchanged for this change.
- `openai-agents` is already present in project dependencies; this change uses the installed SDK boundary without adding a new dependency.
