## 1. Worker Input Builder

- [x] 1.1 Implement `backend/app/workers/worker_input_builder.py` with a public builder entrypoint for Router v1 `WorkerInput`.
- [x] 1.2 Add deterministic worker type to `WorkerMode` mapping for `plc-dev`, `plc-test`, `plc-formal`, and `plc-repair`.
- [x] 1.3 Add deterministic expected output artifact mapping for each worker type.
- [x] 1.4 Implement input artifact selection for `plc-dev` from `raw_user_request` or existing `requirements_ir`.
- [x] 1.5 Implement input artifact selection for `plc-test` and `plc-formal` from current `requirements_ir` and `plc_code`.
- [x] 1.6 Implement input artifact selection for `plc-repair` from current `plc_code` plus latest test or formal failure evidence.
- [x] 1.7 Populate `WorkerContext`, budget, trace context, worker job ID, and idempotency key from `TaskState`.
- [x] 1.8 Add focused unit coverage for builder output validation and missing-input error paths.

## 2. Tool Result And Context Models

- [x] 2.1 Define SDK-independent tool context and compact result models in `backend/app/agents/tools.py`.
- [x] 2.2 Represent tool statuses for applied, rejected, failed, and no-op outcomes.
- [x] 2.3 Add structured guard violation serialization with code, message, and details.
- [x] 2.4 Add compact artifact ref, failure summary, gate state summary, and next action fields.
- [x] 2.5 Ensure result serialization does not include full artifact content unless returned by bounded `read_artifact` full mode.

## 3. Worker Tool Service

- [x] 3.1 Implement a reusable tool service that loads the latest persisted task state for each tool call.
- [x] 3.2 Implement `call_plc_dev` using input builder, Scheduler Guard, MCP adapter, and WorkerResult Handler.
- [x] 3.3 Implement `call_plc_test` using the same guarded worker dispatch path.
- [x] 3.4 Implement `call_plc_formal` using the same guarded worker dispatch path.
- [x] 3.5 Implement `call_plc_repair` using the same guarded worker dispatch path.
- [x] 3.6 Before adapter dispatch, record an active worker job ref and increment active worker and worker call counters.
- [x] 3.7 After terminal result handling, decrement active worker counters and return the persisted post-handler task summary.
- [x] 3.8 On pre-result dispatch failure, restore active job refs and active worker counters to avoid leaked state.
- [x] 3.9 Do not duplicate worker job creation or worker lifecycle events already owned by `McpAdapter`.

## 4. Parallel Worker Tool

- [x] 4.1 Implement `run_parallel_workers` input parsing for a batch of worker types and optional objectives.
- [x] 4.2 Build proposed worker inputs for the entire batch before any side effects.
- [x] 4.3 Validate the entire batch with `validate_parallel_jobs` before dispatching any worker.
- [x] 4.4 Return a rejected batch result without mutation when any batch member violates guard policy.
- [x] 4.5 Dispatch valid batch members through the same worker tool service path and return one compact result per worker.
- [x] 4.6 Preserve v1 behavior that rejects `plc-repair` inside parallel batches.

## 5. Artifact, Gate, And Finish Tools

- [x] 5.1 Implement `read_artifact` summary mode with artifact metadata and no content body.
- [x] 5.2 Implement bounded `read_artifact` full mode for UTF-8 text content with truncation metadata.
- [x] 5.3 Reject `read_artifact` when the artifact is missing or belongs to a different task.
- [x] 5.4 Implement `run_quality_gate` using `QualityGateService.run_quality_gate`.
- [x] 5.5 Return Quality Gate aggregate status, blocking flag, failed gate names, and gate report artifact ref.
- [x] 5.6 Implement `finish_task` with `validate_finish_task` for `succeeded`.
- [x] 5.7 Persist successful terminal task state with phase `completed`, `completed_at`, and a terminal task event.
- [x] 5.8 Return a rejected finish result without mutation when Scheduler Guard rejects successful completion.

## 6. SDK Wrapper Boundary

- [x] 6.1 Add `openai-agents` dependency only if needed for SDK-facing tool decorators in this change.
- [x] 6.2 Expose OpenAI Agents SDK function tool wrappers for all public tools while keeping core service calls SDK-independent.
- [x] 6.3 Use typed function signatures and docstrings so SDK-generated tool schemas are meaningful.
- [x] 6.4 Ensure unit tests can exercise the core tool service without requiring an OpenAI model call.

## 7. Tests

- [x] 7.1 Add `backend/app/tests/unit/test_agent_tools.py` with sqlite-backed repository fixtures matching existing test style.
- [x] 7.2 Test `call_plc_dev` invokes the mock worker, applies the result, and returns compact artifact refs.
- [x] 7.3 Test `call_plc_test` is rejected without current code and causes no worker side effects.
- [x] 7.4 Test `call_plc_repair` is rejected without an open blocking failure and causes no worker side effects.
- [x] 7.5 Test worker dispatch updates completed worker IDs and does not leak active worker refs or active counters.
- [x] 7.6 Test `run_parallel_workers` rejects invalid batches atomically without side effects.
- [x] 7.7 Test `read_artifact` summary and bounded full modes, including truncation metadata.
- [x] 7.8 Test `read_artifact` rejects foreign task artifacts.
- [x] 7.9 Test `run_quality_gate` returns gate assessment summary and gate report artifact ref.
- [x] 7.10 Test `finish_task` rejects succeeded completion with blocking failure or missing Quality Gate.
- [x] 7.11 Test `finish_task` marks a task succeeded after Quality Gate passes.

## 8. Development Script And Verification

- [x] 8.1 Add `scripts/dev_call_agent_tool.py` for invoking individual tools against a local dev database.
- [x] 8.2 Support at least `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, `run_quality_gate`, and `finish_task` in the script.
- [x] 8.3 Print task ID, tool status, summary, artifact refs, gate flags, open failures, and example inspection commands.
- [x] 8.4 Run `uv run pytest backend/app/tests/unit/test_agent_tools.py -q`.
- [x] 8.5 Run `uv run pytest backend/app/tests/unit -q`.
- [x] 8.6 Run `uv run python -m compileall backend`.
- [x] 8.7 Run `uv run python scripts/dev_call_agent_tool.py --tool call_plc_dev` against a local dev database when available.
