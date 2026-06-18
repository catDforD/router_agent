## Context

`docs/backend.md` step 17 calls for end-to-end mock scenario tests that prove Router itself is stable before real MCP workers are connected. The repository already has focused coverage for individual boundaries:

- Runtime lease, checkpoint, resume, and cancellation behavior in `backend/app/tests/integration/test_runtime_loop.py`.
- Main Agent service, fake runner orchestration, function tools, mock worker artifacts, Quality Gate, and finalization behavior in `backend/app/tests/integration/test_main_agent_with_mock_tools.py`.
- Unit coverage for Task API, Artifact Store/API, Event Service/SSE, Scheduler Guard, Quality Gate, Mock MCP Adapter, WorkerResult Handler, and Main Agent output schemas.

What is still missing is a single Router-level scenario suite that starts from task creation and asserts the whole persisted audit trail: task status, worker jobs, artifact types and versions, event ordering, gate results, failures, repair counters, and final reports.

The automated E2E tests should be deterministic. They should validate Router orchestration and persistence behavior, not live model quality or provider compatibility. Live provider smoke checks belong to the separate OpenAI-compatible Main Agent runner change.

## Goals / Non-Goals

**Goals:**

- Add a mock E2E scenario suite for the five step-17 flows:
  - simple development success
  - test failure followed by repair and passing regression
  - formal failure followed by repair, test regression, and formal regression
  - clarification pause
  - repair budget exhaustion ending in `partial_failed`
- Exercise task creation, RuntimeService episode execution, Main Agent tools, mock MCP workers, WorkerResult handling, Quality Gate, artifact persistence, event persistence, worker job persistence, and terminal task persistence in one scenario harness.
- Keep automated tests deterministic by using fake/scripted Main Agent runner decisions.
- Assert observable state through persisted repositories and, where useful, read-only HTTP endpoints.
- Provide a local script for manually running one mock E2E scenario against the configured local database and artifact root.

**Non-Goals:**

- Do not call a live LLM or require `OPENAI_API_KEY` in the automated E2E suite.
- Do not test real MCP worker servers.
- Do not change Router v1 contracts, JSON Schema files, TypeScript declarations, or database tables.
- Do not rely on wall-clock sleeps or long polling to prove background execution.
- Do not replace the existing focused unit and integration tests.

## Decisions

### Use deterministic fake Main Agent runners for E2E

The E2E suite should inject a fake runner into `RuntimeService`, returning structured intake classification and executing a scripted tool sequence through `AgentToolService`.

```text
Task API create
    |
    v
RuntimeService.start_task(task_id, runner=ScriptedE2ERunner)
    |
    v
MainAgentService.run_episode
    |
    v
AgentToolService -> McpAdapter(mock) -> WorkerResultHandler
    |
    v
QualityGate -> final report/log -> terminal task event
```

Alternative considered: run the production OpenAI Agents SDK runner in E2E. Rejected because the goal is Router state-machine stability, and live model behavior would make the suite slow, flaky, credential-dependent, and provider-dependent.

### Keep HTTP creation in scope, but manually drive Runtime execution

The tests should create tasks through `POST /api/tasks` so they cover request validation, TaskService side effects, raw request artifacts, and `task.created` events. They should monkeypatch the scheduled background entrypoint to capture the task ID, then invoke `RuntimeService.start_task` directly with the fake runner.

Alternative considered: let FastAPI `BackgroundTasks` run normally under TestClient. Rejected because TestClient often runs background tasks before returning control to the test, which blurs the API-fast-return behavior and makes runner injection harder.

### Assert persisted audit surfaces, not only returned output

Each scenario should assert the persisted `TaskState`, `worker_jobs`, artifact rows, visible event sequence, and `gate_results`. The returned `MainAgentEpisodeOutput` is useful, but it is not sufficient because step 17 is about replayability from persisted records.

Recommended shared assertions:

- task final `status` and `phase`
- worker job count, worker type sequence, and terminal job statuses
- artifact type set, important version counts, and current artifact pointers
- visible event ordering with monotonic event seq
- Quality Gate records and latest gate report marker
- `repair_rounds`, regression flags, open/resolved failures
- final report and main agent replay log artifacts for terminal episodes

### Use report-first finalization path

Current `MainAgentService` runs tools with `report_first_finalization=True`, then persists final report and replay log before applying the terminal status from `MainAgentEpisodeOutput`. The E2E runner should follow that pattern: use `run_quality_gate`, then return final structured output with `final_task_status` instead of directly applying `finish_task` inside the tool sequence.

Alternative considered: scripted runner calls `finish_task` as a tool. Rejected for E2E because it bypasses the report-first terminal path currently used by `MainAgentService._persist_orchestration_output`.

### Add explicit support for exhausted-repair scenario

The existing mock scenarios cover "failed then repair pass" by making v2 code pass. Step 17 also requires "repair three rounds still fails." That needs deterministic support.

Preferred first implementation: add a mock scenario such as `test_failed_repair_exhausted` where `plc-test` continues to fail after repaired code versions, while `plc-repair` continues to produce new code until the Scheduler Guard rejects a fourth repair.

The E2E runner should then drive:

```text
dev -> test failed -> repair -> test failed -> repair -> test failed
    -> repair -> test failed -> gate failed -> final partial_failed
```

The final state should be `partial_failed`, `repair_rounds == 3`, `gates.has_blocking_failure == true`, and Quality Gate should record blocking failures. This keeps the behavior close to real Router policy instead of simulating the terminal state by direct task mutation.

Alternative considered: inject a custom mock runner only inside the E2E test. This is acceptable if the implementation wants to avoid changing global mock scenario names, but a named mock scenario is more reusable for local scripts.

### Add a local script as a smoke tool, not as the primary automated check

Add `scripts/e2e_run_mock_task.py` to run one deterministic scenario and print a concise summary: task ID, final status, worker jobs, artifacts, events, gate status, and useful curl commands. The script is for developer inspection and should not replace pytest assertions.

## Risks / Trade-offs

- [Risk] E2E tests duplicate integration test logic. -> Mitigation: keep E2E assertions focused on cross-boundary persisted audit surfaces and scenario matrices, while leaving low-level behavior in existing tests.
- [Risk] Fake runner hides prompt or model regressions. -> Mitigation: this suite intentionally tests Router determinism; live model/provider checks remain separate smoke tests.
- [Risk] E2E tests become brittle if they assert exact full event lists. -> Mitigation: assert required subsequences and monotonic ordering for critical events, while allowing extra observability events.
- [Risk] Exhausted-repair support could overfit mock behavior. -> Mitigation: encode only deterministic failure/pass behavior needed to exercise Router repair-limit policy, without changing production logic.
- [Risk] Test runtime may grow as scenarios expand. -> Mitigation: use SQLite file databases, in-process mock workers, no sleeps, and focused artifact/event assertions.

## Migration Plan

No runtime migration is required. The change is test and local-script only.

Implementation can land in small steps:

1. Add reusable E2E fixtures and scripted runner helpers.
2. Add happy path, repair path, formal repair path, and clarification tests using existing mock scenarios.
3. Add deterministic exhausted-repair support and its E2E test.
4. Add the local E2E smoke script.
5. Run focused E2E, integration, unit, compile, and whitespace checks.

Rollback removes the new E2E tests, helper code, local script, and any added mock scenario while leaving existing Router runtime behavior unchanged.

## Open Questions

- Should the exhausted-repair behavior be a named mock scenario in `mock_worker.py`, or should it be isolated to an E2E-only custom mock runner?
- Should the E2E file live under `backend/app/tests/e2e/` to match the existing test layout, or under top-level `tests/e2e/` to match the original document wording?
- Should the local smoke script create tasks through direct service calls for simplicity, or through a running HTTP API for closer manual parity with frontend behavior?
