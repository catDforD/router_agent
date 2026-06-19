## Context

The backend currently runs Main Agent episodes through the OpenAI Agents SDK. Intake and orchestration agents are constructed with `output_type`, and SDK Chat Completions execution converts that output schema into `response_format`. This makes production Main Agent execution depend on structured-output provider support even though the rest of the Router workflow already uses explicit tools, persisted events, artifact writes, Scheduler Guard, Quality Gate, and WorkerResult handling as the durable source of truth.

The worker simulation path is separate: the PLC MCP worker server already uses an OpenAI-compatible Chat Completions client through `DEEPSEEK_*` settings. This change should not merge those settings. Main Agent model execution needs its own provider configuration and execution loop.

The frontend target is a transcript-like execution view: public agent messages, tool calls, tool results, edits/artifacts, verification steps, and completion. This must not expose hidden chain-of-thought. The backend should persist and stream only user-visible progress messages, rationale summaries, tool arguments/results, artifact IDs, and final report references.

## Goals / Non-Goals

**Goals:**

- Run the Main Agent through OpenAI-compatible Chat Completions using messages, tool calling, and optional streaming.
- Remove the production requirement for Responses API, OpenAI Agents SDK structured outputs, and Chat Completions `response_format`.
- Replace the separate structured Intake LLM phase with normal Main Agent planning and tool-driven state changes.
- Make final report generation and terminal status application tool-driven and guarded.
- Stream and persist public Main Agent messages and step events suitable for frontend timeline rendering.
- Preserve existing Runtime lease/checkpoint behavior, WorkerResult handling, Artifact Store, Quality Gate, Scheduler Guard, task APIs, and worker MCP contracts.

**Non-Goals:**

- Do not expose hidden chain-of-thought, raw model internals, API keys, full PLC code, full logs, or unbounded artifact contents in events.
- Do not change the PLC worker MCP server contract or reuse `DEEPSEEK_*` settings for Main Agent execution.
- Do not remove deterministic mock tests or require provider credentials for the default backend test/eval suite.
- Do not require the first implementation to support every possible OpenAI-compatible provider extension beyond tool calling.

## Decisions

### Replace structured-output episodes with a Chat Completions tool loop

Introduce a Main Agent runner that owns a conversation loop over an OpenAI-compatible Chat Completions client:

```text
system instructions
task state view
model assistant message / tool_calls
tool result messages
...
final report tool call
finish task tool call
```

The runner MUST NOT pass `response_format` for Main Agent model calls. It should accept streaming and non-streaming clients, because some OpenAI-compatible providers have incomplete streaming tool-call support.

Alternative considered: keep OpenAI Agents SDK and configure `OpenAIProvider(use_responses=False)`. Rejected for this change because the SDK still relies on `output_type` for the current episode contract, and the target provider compatibility specifically avoids `response_format`.

### Make tools, not model final JSON, the authoritative side-effect boundary

The model should no longer return `MainAgentEpisodeOutput` as the success path. Instead:

- `update_plan` records a public plan.
- `request_clarification` persists open questions and moves the task to `waiting_user`.
- Existing worker tools dispatch PLC subagents and return compact results.
- `run_quality_gate` persists validation evidence.
- `write_final_report` writes `FINAL_REPORT` and `MAIN_AGENT_LOG`.
- `finish_task` applies terminal status only after guard checks and durable report artifacts.

The model may still produce normal assistant text, but text alone is never authoritative for task state.

Alternative considered: parse a free-form final assistant JSON block without `response_format`. Rejected because it reintroduces fragile model-output parsing where guarded tools can provide deterministic validation.

### Keep finalization report-first

Successful or intentional terminal finalization must continue to write a user-visible final report before terminal task events. The new path should make this an explicit tool contract:

```text
run_quality_gate
write_final_report
finish_task
```

`finish_task(succeeded)` must reject if Quality Gate has not passed, a required final report is missing, blocking failures remain, required test/formal evidence is absent, or regression requirements are pending.

Alternative considered: let `finish_task` also synthesize the report. Rejected because separating `write_final_report` from `finish_task` makes report durability and terminal mutation order easier to test and observe.

### Replace standalone intake with agent planning and guarded tools

Task API creation may remain conservative (`created`, `intake`, `unknown`, `L0`), but Runtime no longer needs a separate structured intake model call before orchestration. The Main Agent starts with the full state view, emits a public plan, and either:

- updates task planning metadata through tools,
- requests clarification,
- or proceeds to guarded worker calls.

Scheduler Guard must be adjusted so the tool-loop path can safely move a created/intake task into a runnable state without requiring a prior structured classification object. Safety-critical gating remains deterministic and should be enforced through heuristics, tool inputs, Quality Gate, and guarded finalization rather than trusting unvalidated model labels.

Alternative considered: keep the intake phase but implement it as plain JSON in assistant text. Rejected because the user explicitly wants the Main Agent to plan and decide flow through normal tool use.

### Add public Main Agent message and step events

The observability recorder should support a user-visible message stream:

```text
main_agent.message
main_agent.step_started
main_agent.tool_called
main_agent.tool_result
main_agent.step_completed
main_agent.completed
```

The first implementation may batch token deltas into complete `main_agent.message` events if token-level streaming is not necessary. If token/delta events are added, they must be bounded and replayable without requiring clients to inspect raw provider chunks.

The replay log should store the same normalized public entries plus compact internal metadata needed for diagnosis. It must explicitly exclude hidden chain-of-thought.

Alternative considered: reuse only `main_agent.decision` events for all agent messages. Rejected because frontend transcript rendering needs a stable distinction between public agent narration, tool calls, and completion.

### Separate Main Agent provider settings from worker provider settings

Add Main Agent provider settings such as:

```text
MAIN_AGENT_PROVIDER=openai_compatible
MAIN_AGENT_API_KEY=...
MAIN_AGENT_BASE_URL=https://provider.example/v1
MAIN_AGENT_MODEL=...
MAIN_AGENT_TIMEOUT_SECONDS=120
MAIN_AGENT_MAX_TURNS=40
MAIN_AGENT_STREAM=true
```

`OPENAI_API_KEY` can remain as a compatibility fallback for official OpenAI usage, but `DEEPSEEK_*` remains scoped to worker simulation. Diagnostics must redact all keys and secret-bearing URLs.

Alternative considered: reuse `DEEPSEEK_*` for Main Agent. Rejected because worker simulation and Main Agent orchestration have different operational roles, models, timeouts, and debugging needs.

## Risks / Trade-offs

- [Risk] Some OpenAI-compatible providers claim tool-call support but produce malformed tool call arguments. -> Mitigation: parse and validate tool arguments through Pydantic/tool schemas, emit observable tool errors, and keep max-turn/error terminalization guarded.
- [Risk] Streaming tool-call chunks differ across providers. -> Mitigation: implement a non-streaming path first or as fallback; normalize streaming chunks only after complete tool calls can be reconstructed.
- [Risk] Removing standalone intake weakens early deterministic task classification. -> Mitigation: move required safety and completion checks into tools, Scheduler Guard, Quality Gate, and finalization validation; add eval invariants for required test/formal evidence.
- [Risk] Public progress messages could accidentally expose hidden reasoning if prompts are loose. -> Mitigation: instructions must ask for concise user-visible progress only, and recorder must store bounded content without raw hidden reasoning fields.
- [Risk] Tool-driven finalization increases the number of required model actions. -> Mitigation: instructions and tests should enforce a short finalization sequence; max-turn handling should write a deterministic failure report.
- [Risk] Router v1 event contract changes affect frontend consumers. -> Mitigation: add schema and TypeScript declarations in the same change, and keep existing event values stable.

## Migration Plan

1. Add the new Chat Completions runner behind the existing `MainAgentRunner` boundary while keeping fake runner tests possible.
2. Add provider settings and redacted diagnostics without changing worker `DEEPSEEK_*` settings.
3. Add tool-loop finalization tools and public observability events.
4. Update Runtime/MainAgentService to use the new runner path for production execution.
5. Update schemas, TypeScript declarations, tests, and eval scaffolding.
6. Keep rollback simple by retaining the existing mock worker path and deterministic tests; if live provider compatibility fails, disable the new production runner through configuration while preserving persisted task/event/artifact contracts.

## Open Questions

- Should the first implementation emit token-level `main_agent.message_delta` events, or only complete public assistant messages?
- Should `update_plan` be a required tool call before any worker dispatch, or can the model use ordinary assistant text for simple QA tasks?
- What minimum provider compatibility smoke test should gate live usage: non-streaming tool calls only, or streaming tool calls as well?
- Should the legacy structured-output runner remain temporarily selectable for comparison, or be removed from production configuration immediately?
