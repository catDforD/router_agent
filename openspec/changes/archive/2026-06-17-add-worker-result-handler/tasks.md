## 1. Handler Structure

- [x] 1.1 Add `WorkerResultHandler` and a public `handle_worker_result` entrypoint in `backend/app/workers/worker_result_handler.py`.
- [x] 1.2 Define a small result dataclass that returns the updated `TaskState`, applied/no-op status, and a concise summary for future agent tools.
- [x] 1.3 Add handler-specific exceptions for identity mismatch, missing task/job, and invalid produced artifact references.
- [x] 1.4 Wire the handler to existing `TaskRepository`, `WorkerJobRepository`, and `ArtifactRepository` without adding new persistence tables.

## 2. Validation And Idempotency

- [x] 2.1 Validate that `WorkerResult.task_id`, `worker_job_id`, `worker_type`, and `mcp_tool` match the persisted worker job input/result context.
- [x] 2.2 Validate that every `WorkerResult.produced_artifacts` entry exists in the artifact repository and belongs to the same task.
- [x] 2.3 Treat results whose `worker_job_id` is already in `TaskState.completed_worker_job_ids` as no-op applications.
- [x] 2.4 Deduplicate merged artifact IDs, failures, assumptions, clarification questions, and completed worker job IDs by stable IDs.
- [x] 2.5 Remove the worker job from `active_worker_jobs` and add it to `completed_worker_job_ids` on handled terminal results.

## 3. Artifact And Gate Projection

- [x] 3.1 Implement artifact-type projection from produced `ArtifactRef` values into `TaskState.current_artifacts`.
- [x] 3.2 Apply `plc-dev` passed semantics for requirements, current code, I/O contract, stale verification markers, and conservative phase progression.
- [x] 3.3 Apply `plc-test` passed semantics for latest report, `latest_test_passed`, `regression_required`, and same-source failure resolution.
- [x] 3.4 Apply `plc-test` failed semantics for latest report/failing trace, open blocking failures, and blocking gate state.
- [x] 3.5 Apply `plc-formal` passed semantics for latest report, `latest_formal_passed`, `formal_regression_required`, and same-source failure resolution.
- [x] 3.6 Apply `plc-formal` failed semantics for latest report/counterexample, open blocking failures, and blocking gate state.
- [x] 3.7 Apply `plc-repair` passed semantics for patch, patched code, repair summary, one-time `repair_rounds` increment, regression flags, and stale pass marker invalidation.
- [x] 3.8 Recalculate `gates.has_blocking_failure` from open blocking failures after every non-no-op application.
- [x] 3.9 Clear `gates.can_finish_as_success` whenever worker result handling mutates runtime evidence or gate state.

## 4. Clarification And Error Paths

- [x] 4.1 Apply `need_clarification` outcomes by merging questions and moving the task to `waiting_user` / `clarifying`.
- [x] 4.2 Handle `timeout`, `error`, `partial`, and `cancelled` execution statuses without applying normal worker pass/fail semantics.
- [x] 4.3 Preserve existing task artifacts, failures, and test/formal pass markers for timeout/error results unless valid failures are explicitly present.
- [x] 4.4 Ensure all updated task states pass `TaskState` model validation before persistence.

## 5. Unit Tests

- [x] 5.1 Add `backend/app/tests/unit/test_worker_result_handler.py` with sqlite-backed repository fixtures matching existing test style.
- [x] 5.2 Test that `plc-dev` passed updates `current_code`, `current_io_contract`, and produced artifact IDs.
- [x] 5.3 Test that failed `plc-test` appends a blocking failure, sets `latest_test_passed=false`, and sets `has_blocking_failure=true`.
- [x] 5.4 Test that failed `plc-formal` records `latest_counterexample`, appends a formal failure, and sets `latest_formal_passed=false`.
- [x] 5.5 Test that passed `plc-repair` updates `current_code` to version 2, records patch/summary refs, increments `repair_rounds`, and sets `regression_required=true`.
- [x] 5.6 Test that repair after an open formal failure sets `formal_regression_required=true`.
- [x] 5.7 Test idempotent reapplication does not duplicate failures or increment repair rounds twice.
- [x] 5.8 Test passing test/formal results resolve same-source open failures and recalculate `has_blocking_failure`.
- [x] 5.9 Test clarification and timeout/error handling paths.
- [x] 5.10 Test that a foreign or missing produced artifact reference is rejected without task mutation.

## 6. Development Script And Verification

- [x] 6.1 Add `scripts/dev_worker_result_chain.py` to run a mock scenario through adapter results and handler state application.
- [x] 6.2 Support at least `dev_test_pass`, `test_failed_then_repair_pass`, and `formal_failed_then_repair_pass` scenarios in the script.
- [x] 6.3 Print task ID, handled worker jobs, current artifact pointers, gate flags, open failures, repair rounds, and example inspection commands.
- [x] 6.4 Run `uv run pytest backend/app/tests/unit/test_worker_result_handler.py -q`.
- [x] 6.5 Run `uv run pytest backend/app/tests/unit -q`.
- [x] 6.6 Run `uv run python -m compileall backend`.
- [x] 6.7 Run `python scripts/dev_worker_result_chain.py --scenario formal_failed_then_repair_pass` against a local dev database when available.
