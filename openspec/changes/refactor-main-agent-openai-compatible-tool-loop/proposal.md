## Why

The current Main Agent execution path depends on OpenAI Agents SDK structured outputs, which map to provider features such as Responses API or Chat Completions `response_format`. This blocks OpenAI-compatible providers that support normal chat and tool calling but not structured output.

We need the backend to run the Main Agent natively through OpenAI-compatible Chat Completions, expose user-visible agent progress like a working transcript, and let deterministic Router tools persist final reports and terminal state instead of requiring the model to return a validated episode object.

## What Changes

- **BREAKING**: Main Agent production execution no longer requires a separate Intake LLM classification stage before orchestration.
- **BREAKING**: Main Agent production execution no longer requires `Agent(output_type=...)`, `MainAgentEpisodeOutput`, Responses API, or Chat Completions `response_format`.
- Add a Chat Completions tool-loop runner for Main Agent execution using OpenAI-compatible `messages`, `tools`, `tool_calls`, and optional streaming.
- Add Main Agent provider settings for OpenAI-compatible endpoints, keeping them separate from `DEEPSEEK_*` worker simulation settings.
- Move final report creation and terminal task completion behind explicit Main Agent tools guarded by Scheduler Guard and Quality Gate.
- Add public Main Agent message and step events so the frontend can render a progress transcript with agent messages, tool calls, tool results, report creation, and completion.
- Preserve existing worker dispatch, MCP adapter, WorkerResult handling, Artifact Store, Quality Gate, Scheduler Guard, task APIs, and event streaming boundaries.
- Keep hidden chain-of-thought private. Only user-visible summaries, rationale, and progress messages are persisted or streamed.

## Capabilities

### New Capabilities

- `main-agent-openai-compatible-tool-loop`: Main Agent provider configuration and Chat Completions tool-loop execution without structured model outputs.

### Modified Capabilities

- `main-agent-service`: Replace structured-output episode execution with a tool-driven orchestration loop and tool-based finalization.
- `main-agent-function-tools`: Add plan, clarification, public progress, final report, and terminalization tools while preserving guarded worker/gate tools.
- `main-agent-turn-observability`: Stream and persist user-visible Main Agent messages and steps without exposing hidden chain-of-thought.
- `task-intake-classification`: Remove the requirement that Runtime obtains a separate structured intake classification before worker execution.
- `runtime-loop-background-execution`: Run the new Chat Completions Main Agent loop through the existing lease/checkpoint/runtime boundary.
- `event-streaming-api`: Ensure frontend SSE can replay and tail the new public Main Agent message/step events.
- `router-v1-schema-contract`: Add or update Router v1 event/schema contract entries required for public Main Agent messages and tool-loop completion.
- `backend-eval-suite`: Update deterministic eval scaffolding to verify the tool-loop runner behavior without provider credentials.

## Impact

- Affected backend code: `backend/app/agents/main_agent.py`, `backend/app/agents/tools.py`, `backend/app/agents/observability.py`, `backend/app/agents/instructions.py`, `backend/app/agents/output_schema.py`, `backend/app/core/config.py`, and runtime/eval tests.
- Affected contracts: Router event types and schemas for public Main Agent message/step events, plus TypeScript declarations under `schema/ts/`.
- Affected runtime behavior: Main Agent tasks finish only through guarded tools that write final artifacts and apply terminal state.
- Provider requirements change from Responses/structured-output support to OpenAI-compatible Chat Completions with tool calling; streaming is supported when available but should have a non-streaming fallback.
- Existing PLC worker MCP and `DEEPSEEK_*` worker simulation settings remain separate and unchanged.
