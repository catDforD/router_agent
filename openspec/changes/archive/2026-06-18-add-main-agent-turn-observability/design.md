## Context

The current Runtime Loop can execute Main Agent episodes in the background and stream persisted task events to the frontend. A successful run exposes task creation, intake classification, worker lifecycle events, artifact creation, Quality Gate results, and terminal task status. What is missing is the model orchestration layer between those events: each Main Agent turn's public decision summary, selected tool, tool observation returned to the model, and the final episode report.

The OpenAI Agents SDK already supports a tool-use loop and has `Runner.run_streamed(...)`, semantic stream events, and lifecycle hooks. However, Router should not expose SDK-native event shapes directly because those shapes are provider/SDK details. Router events and artifacts are the stable replay and frontend API surface.

The change also needs to avoid a consistency gap in the current finalization shape. Today `finish_task` can mark a task terminal before the final `MainAgentEpisodeOutput` is persisted. If the final structured output is invalid or the process fails after terminal status is written, the task can show `succeeded` without a durable Main Agent report.

## Goals / Non-Goals

**Goals:**

- Stream user-visible Main Agent orchestration progress through the existing task SSE endpoint.
- Capture per-turn public rationale summaries, selected tools, sanitized tool arguments, tool results, and artifact/failure references.
- Persist a complete replay log as a `MAIN_AGENT_LOG` artifact.
- Persist the final Main Agent episode report as a `FINAL_REPORT` artifact.
- Emit a `main_agent.completed` event that references the final report and replay log.
- Make Runtime responsible for validating and persisting the final output before marking the task terminal.
- Keep hidden model chain-of-thought private; only bounded public summaries are persisted and streamed.
- Preserve current worker, artifact, Quality Gate, Scheduler Guard, and event persistence boundaries.

**Non-Goals:**

- Do not expose raw hidden reasoning or provider-private chain-of-thought.
- Do not stream token-level deltas as a stable public API in this change.
- Do not add new public HTTP endpoints.
- Do not add a new artifact type; use existing `FINAL_REPORT` and `MAIN_AGENT_LOG`.
- Do not add database tables.
- Do not implement a durable external queue.
- Do not solve all OpenAI-compatible provider behavior; coordinate with `add-openai-compatible-main-agent-runner` but keep this change focused on observability.

## Decisions

### Use Router events as the frontend streaming contract

Translate SDK stream/hook signals and tool wrapper boundaries into Router events instead of exposing raw SDK events directly.

```text
Runner.run_streamed / RunHooks
        |
        v
MainAgentObservabilityRecorder
        |
        +--> EventService.append_event(...)
        +--> in-memory turn log buffer
        +--> ArtifactStore.write_artifact_content(...)
```

Router events are already append-only, sequenced per task, and streamed through SSE with resume support. This keeps frontend behavior stable even if the SDK changes event names.

Alternative considered: send SDK events directly over SSE. Rejected because SDK stream events include provider-specific raw response details and do not match Router's durable event contract.

### Persist public rationale summaries, not hidden chain-of-thought

The model should provide a bounded `rationale_summary` for each tool decision. This is an intentional, public explanation field, not hidden reasoning. Instructions should explicitly forbid copying hidden chain-of-thought and require concise decision summaries suitable for user/audit display.

For tool calls, the most reliable place to capture the summary is the tool argument schema:

```text
call_plc_dev(
    task_id,
    objective,
    rationale_summary,
)
```

The recorder can then emit the decision and tool-call event atomically from the same tool invocation.

Alternative considered: ask the model to emit a free-form message before each tool call. Rejected because models may skip the message, duplicate it, or produce text that is not tied atomically to the actual tool call.

### Keep high-frequency details out of events

Events should contain compact, user-visible data:

- turn index
- phase
- tool name
- public rationale summary
- objective or final status
- status/result summary
- artifact IDs
- failure IDs
- worker job ID when available

Larger replay details, sanitized raw SDK run items, and full final `MainAgentEpisodeOutput` should be written to artifacts. This keeps SSE efficient and preserves replay depth.

Alternative considered: place the complete run transcript in event payloads. Rejected because events are timeline records and should stay small enough for frontend streams and history reads.

### Use `FINAL_REPORT` for the final Main Agent report

The final user-facing report should be stored as an artifact with type `FINAL_REPORT`. The artifact content should include the validated `MainAgentEpisodeOutput` plus report metadata such as task ID, main agent run ID, final status, summary, referenced artifacts, gate summary, and creation time.

`MAIN_AGENT_LOG` should store the larger turn replay log. The `main_agent.completed` event should reference both artifact IDs.

Alternative considered: add a new `main_agent_report` artifact type. Rejected because the existing Router contract already has `FINAL_REPORT` and `MAIN_AGENT_LOG`; adding a new type would increase schema churn without clear benefit.

### Move successful terminal status after report persistence

Runtime should become the successful finalization authority:

```text
orchestration agent returns MainAgentEpisodeOutput
    |
    v
validate output
    |
    v
write FINAL_REPORT artifact
write MAIN_AGENT_LOG artifact
emit main_agent.completed
    |
    v
validate finish policy
mark task.succeeded / partial_failed / failed
emit terminal task event
```

This is the selected "方案 A". The model may recommend a final status in `MainAgentEpisodeOutput`, but Runtime performs persistence and final state transition after the report is durable.

Alternative considered: keep `finish_task` as the tool that marks success and then write the report later. Rejected because it can create a terminal task without a final report if final output validation or persistence fails.

### Retain worker and gate tools, but change terminal finish semantics

Worker tools and `run_quality_gate` remain model-callable. `finish_task` should no longer be the primary success path for orchestration, or should be restricted to a non-terminal finalization request that Runtime completes after final output validation.

For compatibility, direct tool tests may still exercise guarded terminal transitions, but Main Agent orchestration instructions should tell the model to return a final `MainAgentEpisodeOutput` after a passing Quality Gate rather than calling terminal `finish_task(succeeded)`.

Alternative considered: remove `finish_task` from tool registration immediately. Rejected as potentially disruptive to existing tests and repair/failure flows; a staged restriction is safer.

### Add streaming runner support at the MainAgentRunner boundary

The official OpenAI path should use `Runner.run_streamed(...)` for orchestration so progress can be observed while the run is active. The runner should consume `stream_events()` and/or hooks to record:

- model turn start/end
- tool selected
- tool output
- final output
- model/provider errors

The existing synchronous runner behavior can remain for tests or fallback, but production Runtime should prefer the streaming path when observability is enabled.

Alternative considered: wait until a later provider compatibility runner. Rejected because official SDK streaming is available now and the event/log contract should be designed before adding more runner modes.

## Risks / Trade-offs

- [Risk] Public rationale summaries can be too verbose or accidentally include sensitive content. -> Mitigation: bound length, instruct the model to summarize only decision-relevant facts, and sanitize/truncate event payloads before persistence.
- [Risk] SDK stream events differ between Responses and chat-completions modes. -> Mitigation: keep SDK events behind a Router recorder interface and rely on tool argument summaries for stable rationale capture.
- [Risk] Moving terminal success out of `finish_task` changes existing orchestration behavior. -> Mitigation: stage the change with tests, keep guard validation, and preserve direct finish behavior outside model orchestration if needed.
- [Risk] Events may become noisy for long repair loops. -> Mitigation: emit one decision/tool/result event per meaningful action rather than token-level deltas; store detailed replay in `MAIN_AGENT_LOG`.
- [Risk] Final report persistence can fail after work completed but before terminal status. -> Mitigation: leave the task non-terminal with an observable error rather than falsely marking success without a report.
- [Risk] OpenAI-compatible runner work may need a different streaming mechanism. -> Mitigation: define a runner-neutral observability recorder and adapt each runner to it.

## Migration Plan

No database migration is required.

1. Add Router event vocabulary and schema fixture coverage for Main Agent turn/tool/completion events.
2. Add a Main Agent observability recorder that can append compact events and collect replay log entries.
3. Add rationale summary fields to Main Agent tool wrappers and instructions.
4. Add final report and main agent log artifact writing after final output validation.
5. Move model-orchestration success finalization to Runtime after report persistence.
6. Switch official orchestration execution to `Runner.run_streamed(...)` with recorder integration.
7. Add integration tests for the happy path event order and report artifacts.

Rollback can disable the recorder and return to synchronous final-output handling, but tasks created while observability is enabled may retain additional events and artifacts.

## Open Questions

- Should `finish_task` remain registered for Main Agent orchestration at all, or should it be removed once Runtime owns terminal success?
- Should `main_agent.completed` be emitted before or after the terminal `task.succeeded` event? The design currently prefers before terminal status so the report exists first.
- Should internal raw SDK stream events be stored in `MAIN_AGENT_LOG`, or should the log contain only Router-normalized entries?
- How much of the final report should be user-visible by default versus internal-only metadata?
