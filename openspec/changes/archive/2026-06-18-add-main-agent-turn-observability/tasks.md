## 1. Contract And Schema Surface

- [x] 1.1 Add Router event types for `main_agent.turn_started`, `main_agent.tool_called`, `main_agent.tool_result`, and `main_agent.completed` in Python models.
- [x] 1.2 Regenerate or update Router JSON Schema exports for event and artifact contract changes.
- [x] 1.3 Update TypeScript Router contract declarations with the new Main Agent observability event type values.
- [x] 1.4 Add schema fixture or model tests proving new Main Agent event payloads validate.
- [x] 1.5 Confirm existing `final_report` and `main_agent_log` artifact types require no new artifact enum value.

## 2. Observability Recorder

- [x] 2.1 Add a Main Agent observability recorder abstraction that receives task ID, main agent run ID, session, artifact root, and checkpoint callback.
- [x] 2.2 Implement compact event append helpers for turn start, tool call, tool result, completed, and error observability events.
- [x] 2.3 Implement bounded sanitization for rationale summaries, tool arguments, tool results, artifact references, and failure references.
- [x] 2.4 Implement replay log entry collection using Router-normalized entries rather than raw frontend-facing SDK objects.
- [x] 2.5 Add unit tests for recorder event payload shape, visibility, correlation IDs, truncation, and checkpoint behavior.

## 3. Tool Rationale Capture

- [x] 3.1 Add optional `rationale_summary` parameters to worker tool wrappers and service methods.
- [x] 3.2 Add optional `rationale_summary` parameters to `run_parallel_workers`, `run_quality_gate`, and any orchestration-facing finalization helper.
- [x] 3.3 Update Main Agent orchestration instructions to require concise public rationale summaries for tool calls and forbid hidden chain-of-thought disclosure.
- [x] 3.4 Wire tool call rationale and compact tool results into the observability recorder.
- [x] 3.5 Add unit tests proving rationale summaries are recorded without changing worker input contracts or forwarding rationale as worker objective unless explicitly intended.

## 4. Streaming Runner Integration

- [x] 4.1 Extend the Main Agent runner boundary to support streaming orchestration while preserving deterministic fake runner tests.
- [x] 4.2 Implement official OpenAI Agents SDK orchestration with `Runner.run_streamed(...)` when observability is enabled.
- [x] 4.3 Translate SDK stream events or lifecycle hook callbacks into normalized recorder entries without exposing SDK raw event shapes to frontend APIs.
- [x] 4.4 Preserve `MainAgentEpisodeOutput` validation using `final_output_as(...)` after the streamed run completes.
- [x] 4.5 Add tests with fake streaming events proving turn, tool call, tool result, and final output are recorded in order.

## 5. Report-First Finalization

- [x] 5.1 Add final report writer logic that stores validated `MainAgentEpisodeOutput` as a user-visible `FINAL_REPORT` artifact.
- [x] 5.2 Add replay log writer logic that stores normalized turn entries as a `MAIN_AGENT_LOG` artifact with appropriate visibility.
- [x] 5.3 Emit `main_agent.completed` after report artifacts are persisted and before terminal task success is applied.
- [x] 5.4 Move model-orchestration successful terminal mutation into Runtime after final output validation, report persistence, and Scheduler Guard finalization validation.
- [x] 5.5 Update orchestration instructions so successful completion returns final structured output after Quality Gate instead of relying on terminal `finish_task(succeeded)` as the primary success path.
- [x] 5.6 Preserve direct guarded `finish_task` behavior for non-orchestration callers or tests that intentionally exercise the tool.
- [x] 5.7 Add regression tests proving invalid final output or report persistence failure does not mark the task `succeeded`.

## 6. API And Event Stream Coverage

- [x] 6.1 Add event stream tests proving `main_agent.tool_called`, `main_agent.tool_result`, and `main_agent.completed` appear through `GET /api/tasks/{task_id}/events`.
- [x] 6.2 Add replay tests proving `Last-Event-ID` resumes across Main Agent observability events.
- [x] 6.3 Add artifact API tests proving `FINAL_REPORT` is readable and large replay log content remains artifact-backed.
- [x] 6.4 Add integration test for successful mock path event order: report artifacts, `main_agent.completed`, then `task.succeeded`.
- [x] 6.5 Add integration test for guard rejection visibility through `main_agent.tool_result`.

## 7. Verification

- [x] 7.1 Run focused unit tests for schema, recorder, tools, final report writing, and event streaming.
- [x] 7.2 Run Main Agent integration tests with mock tools and Runtime Loop coverage.
- [x] 7.3 Run existing task API, event API, artifact API, Scheduler Guard, and Quality Gate tests.
- [x] 7.4 Run `uv run python -m compileall backend`.
- [x] 7.5 Run `git diff --check`.
- [x] 7.6 Run a real-service smoke test with the configured OpenAI Responses API and mock MCP workers to confirm streamed observability events and final report artifacts are visible.
