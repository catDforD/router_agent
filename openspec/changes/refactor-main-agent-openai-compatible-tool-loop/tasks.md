## 1. Configuration And Provider Client

- [x] 1.1 Add Main Agent provider settings for API key, base URL, model, timeout, max turns, and streaming preference without reusing `DEEPSEEK_*`.
- [x] 1.2 Update redacted diagnostics and logging tests so Main Agent provider secrets are never logged.
- [x] 1.3 Add an OpenAI-compatible Chat Completions client wrapper with fake client injection for deterministic tests.
- [x] 1.4 Add request construction tests proving Main Agent model requests include messages and tools but do not include `response_format`.

## 2. Chat Completions Tool-Loop Runner

- [x] 2.1 Implement a Main Agent runner that executes a bounded Chat Completions conversation loop over assistant messages, tool calls, and tool results.
- [x] 2.2 Add robust tool-call argument parsing, validation errors, and observable provider/tool-loop failure handling.
- [x] 2.3 Add non-streaming tool-loop support as the baseline provider compatibility path.
- [x] 2.4 Add streaming support or a streaming adapter that normalizes public assistant content and complete reconstructed tool calls.
- [x] 2.5 Add max-turn handling that records an observable failure and prevents false success.

## 3. Main Agent Tools And Finalization

- [x] 3.1 Add `update_plan` support that emits public plan events with bounded plan steps.
- [x] 3.2 Add `request_clarification` support that persists questions, moves the task to `waiting_user`, and emits clarification/task waiting events.
- [x] 3.3 Add `write_final_report` support that writes `FINAL_REPORT` and `MAIN_AGENT_LOG` artifacts from tool arguments.
- [x] 3.4 Update `finish_task` so terminal status requires Scheduler Guard approval and durable final report evidence.
- [x] 3.5 Update tool registration and tool schemas for the Chat Completions runner while preserving existing worker, artifact read, and Quality Gate tool behavior.
- [x] 3.6 Update Main Agent instructions to require public progress messages, report-first finalization, and no hidden chain-of-thought.

## 4. Observability, Events, And Contracts

- [x] 4.1 Add public Main Agent message and optional step event types to backend Router models.
- [x] 4.2 Update JSON Schema exports and TypeScript declarations for new or modified Main Agent observability events.
- [x] 4.3 Extend `MainAgentObservabilityRecorder` to record public assistant messages, plan updates, tool calls, tool results, report creation, completion, and failures.
- [x] 4.4 Ensure replay logs contain normalized public entries and exclude hidden reasoning, raw provider chunks, secrets, and unbounded artifact content.
- [x] 4.5 Add SSE/event-stream tests for replaying and tailing public Main Agent messages, tool calls, tool results, and completion events.

## 5. Runtime And Main Agent Service Integration

- [x] 5.1 Replace production structured-output Main Agent execution with the new Chat Completions tool-loop runner behind the service boundary.
- [x] 5.2 Remove the required standalone structured Intake LLM path from production Runtime execution.
- [x] 5.3 Adjust task state transitions so newly created tasks can enter tool-loop orchestration and clarification can pause through tools.
- [x] 5.4 Preserve Runtime lease claiming, checkpointing, cancellation safety, resume-after-user-message behavior, and terminal-task no-op behavior.
- [x] 5.5 Ensure Runtime checkpoints public Main Agent progress events before final task completion.

## 6. Tests, Eval, And Verification

- [x] 6.1 Update unit tests for settings, provider request construction, tool-call parsing, final report tool validation, and finish-task report requirements.
- [x] 6.2 Update Main Agent service tests to use scripted tool-loop steps instead of structured intake/orchestration outputs.
- [x] 6.3 Update integration and mock E2E tests for dev/test/gate/report/finish happy path, clarification path, repair/regression path, and max-turn failure path.
- [x] 6.4 Update backend eval scaffolding so deterministic evals use scripted tool-loop steps and assert no provider credentials are required.
- [x] 6.5 Run `uv run pytest backend/app/tests/unit -q` and targeted integration/eval tests affected by Main Agent execution.
- [x] 6.6 Run `uv run python -m compileall backend` and `git diff --check`.
