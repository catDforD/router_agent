## 1. Configuration and Internal Shapes

- [x] 1.1 Add typed `MOCK_SCENARIO` support to backend settings with default `dev_test_pass`.
- [x] 1.2 Define a small internal mock artifact write-intent shape for artifact type, version, name, content, summary, metadata, visibility, and parent artifact IDs.
- [x] 1.3 Define internal mock result/exception shapes for successful mock output, deterministic timeout, schema-invalid output, and execution errors.

## 2. Mock Worker Behavior

- [x] 2.1 Implement `plc-dev` mock output that produces `requirements_ir:v1`, `plc_code:v1`, and optionally `io_contract:v1`.
- [x] 2.2 Implement `plc-test` mock pass output with `test_report`, passed outcome, diagnostics list, and test metrics.
- [x] 2.3 Implement `plc-test` mock failure output with `test_report`, `failing_trace`, blocking test `Failure`, diagnostics, and repair recommendation.
- [x] 2.4 Implement `plc-formal` mock pass output with `formal_report`, passed outcome, diagnostics list, and formal metrics.
- [x] 2.5 Implement `plc-formal` mock failure output with `formal_report`, `counterexample`, blocking formal `Failure`, diagnostics, and repair recommendation.
- [x] 2.6 Implement `plc-repair` mock output that derives the next code version, then produces `patch`, `repair_summary`, and patched `plc_code`.
- [x] 2.7 Implement `need_clarification` and `worker_timeout` scenario behavior without wall-clock sleeps.

## 3. Worker Result Normalization

- [x] 3.1 Implement normalizer validation that ensures `WorkerResult` task, worker job, worker type, and MCP tool match the original `WorkerInput`.
- [x] 3.2 Implement timeout normalization to `execution_status="timeout"`, `error_code="MCP_TIMEOUT"`, retryable error, and `next_recommended_action="retry"`.
- [x] 3.3 Implement schema-invalid normalization or rejection with `WORKER_SCHEMA_INVALID`.
- [x] 3.4 Implement execution-error normalization with `WORKER_EXECUTION_ERROR`, terminal worker job error status, and no produced artifacts.

## 4. Adapter Lifecycle and Persistence

- [x] 4.1 Implement a mock-mode adapter entrypoint that accepts a validated `WorkerInput`, session, artifact root, and optional scenario override.
- [x] 4.2 Create the running worker job record before mock worker execution.
- [x] 4.3 Append a user-visible `worker.started` event correlated with the worker job and input artifact IDs.
- [x] 4.4 Persist mock artifact write intents through `ArtifactStore.write_artifact_content`.
- [x] 4.5 Convert persisted artifacts to `ArtifactRef` values and insert them into `WorkerResult.produced_artifacts`.
- [x] 4.6 Append user-visible `artifact.created` events for each persisted artifact.
- [x] 4.7 Complete or error the worker job with the normalized `WorkerResult` and correct terminal `WorkerJobStatus`.
- [x] 4.8 Append user-visible `worker.completed`, `worker.error`, or `worker.timeout` events based on the normalized execution status.

## 5. Development Script

- [x] 5.1 Add `scripts/dev_call_mock_worker.py` with `--worker` and `--scenario` arguments.
- [x] 5.2 Make the script create or load a classified development task fixture suitable for valid worker inputs.
- [x] 5.3 Make the script print the worker result summary, produced artifact IDs, worker job ID, and example artifact/event inspection commands.

## 6. Unit Tests

- [x] 6.1 Add unit tests proving `plc-dev` returns a completed result and persists requirements/code artifacts.
- [x] 6.2 Add unit tests proving `plc-test` pass returns a test report and passed metrics.
- [x] 6.3 Add unit tests proving `plc-test` failure returns `test_report`, `failing_trace`, and a blocking `Failure`.
- [x] 6.4 Add unit tests proving `plc-formal` failure returns `formal_report`, `counterexample`, and a blocking `Failure`.
- [x] 6.5 Add unit tests proving `plc-repair` returns `patch`, `repair_summary`, and patched `plc_code:v2`.
- [x] 6.6 Add unit tests proving timeout normalization returns `execution_status="timeout"` and appends `worker.timeout`.
- [x] 6.7 Add unit tests proving worker jobs and user-visible worker/artifact events are persisted for successful mock invocations.
- [x] 6.8 Add unit tests proving invalid worker output is handled through the schema-invalid path.

## 7. Verification

- [x] 7.1 Run `uv run pytest backend/app/tests/unit/test_mcp_adapter_mock.py -q`.
- [x] 7.2 Run `uv run pytest backend/app/tests/unit -q`.
- [x] 7.3 Run `uv run python -m compileall backend`.
- [x] 7.4 Run `git diff --check`.
