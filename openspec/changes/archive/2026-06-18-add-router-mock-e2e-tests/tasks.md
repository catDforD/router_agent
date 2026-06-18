## 1. E2E Harness

- [x] 1.1 Create `backend/app/tests/e2e/test_router_mock_scenarios.py` with isolated SQLite file database, artifact root, FastAPI `TestClient`, and repository helper fixtures.
- [x] 1.2 Add a scripted E2E Main Agent runner helper that returns validated intake classifications and drives `AgentToolService` actions through `RuntimeService`.
- [x] 1.3 Monkeypatch Task API background runtime entrypoints in E2E tests so task creation records scheduling but Runtime execution is invoked explicitly with the scripted runner.
- [x] 1.4 Add shared assertion helpers for final task state, worker job sequences, artifact type/version presence, event subsequences, gate result rows, and monotonic event sequence numbers.

## 2. Core Mock Scenario Tests

- [x] 2.1 Add the simple development success E2E scenario: task creation, runtime start, `plc-dev`, `plc-test`, Quality Gate pass, final report/log, and `succeeded` terminal status.
- [x] 2.2 Add the test-failure repair E2E scenario using `test_failed_then_repair_pass`: dev, failing test, repair, regression test pass, Quality Gate pass, resolved failure, `repair_rounds == 1`, and `succeeded`.
- [x] 2.3 Add the formal-failure repair E2E scenario using `formal_failed_then_repair_pass`: dev, test pass, formal fail, repair, test regression, formal regression pass, cleared formal regression flag, and `succeeded`.
- [x] 2.4 Add the clarification E2E scenario where intake requires clarification, persists open required questions, emits clarification events, creates no worker jobs, and ends `waiting_user`.

## 3. Exhausted Repair Scenario

- [x] 3.1 Add deterministic support for an exhausted test-repair mock path, either as a named mock scenario or an E2E-only custom mock runner.
- [x] 3.2 Add the exhausted-repair E2E scenario: dev, repeated test failures through three repairs, no fourth repair job, blocking failure remains open, Quality Gate fails, and final task status is `partial_failed`.
- [x] 3.3 Add focused unit coverage for any new mock scenario behavior if production mock worker code is extended.

## 4. Local Smoke Script

- [x] 4.1 Add `scripts/e2e_run_mock_task.py` with supported scenario arguments and deterministic scripted runner behavior matching the automated E2E scenarios.
- [x] 4.2 Have the script print task ID, final status, worker job summary, artifact summary, event summary, gate summary, and useful follow-up curl commands.
- [x] 4.3 Ensure unsupported scenario names fail early through argparse or explicit validation before worker jobs are created.

## 5. Verification

- [x] 5.1 Run `uv run pytest backend/app/tests/e2e/test_router_mock_scenarios.py -q`.
- [x] 5.2 Run `uv run pytest backend/app/tests/integration/test_main_agent_with_mock_tools.py backend/app/tests/integration/test_runtime_loop.py -q`.
- [x] 5.3 Run focused unit tests affected by any mock scenario changes, especially `backend/app/tests/unit/test_mcp_adapter_mock.py` and `backend/app/tests/unit/test_worker_result_handler.py`.
- [x] 5.4 Run `uv run python -m compileall backend`.
- [x] 5.5 Run `git diff --check`.
