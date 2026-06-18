## 1. Output Schemas And Instructions

- [x] 1.1 Define internal `IntakeClassificationOutput`, episode output, plan step, decision, and artifact reference models in `backend/app/agents/output_schema.py`.
- [x] 1.2 Add model validation tests for valid QA, L2 development, L3 safety-critical development, repair, and clarification-required classification outputs.
- [x] 1.3 Add validation tests that reject clarification-required output without questions and invalid enum values.
- [x] 1.4 Implement Main Agent intake and orchestration instruction builders in `backend/app/agents/instructions.py`.
- [x] 1.5 Add tests that instructions mention guarded finalization, artifact boundaries, required test/formal policy, repair regression, and max repair rounds.

## 2. State View, Trace, And Events

- [x] 2.1 Add compact task state view construction in `backend/app/agents/main_agent.py`.
- [x] 2.2 Add tests proving the state view contains task identity, goal, task type, difficulty, gates, artifact refs, open failures, repair counters, worker counters, and available tools.
- [x] 2.3 Add tests proving the state view excludes full PLC code, full reports, full counterexamples, full patches, and full logs.
- [x] 2.4 Add Main Agent run ID generation and trace initialization helpers.
- [x] 2.5 Add event builder helpers for `main_agent.started`, `main_agent.decision`, `main_agent.plan_updated`, `main_agent.clarification_requested`, and `main_agent.finalizing`.
- [x] 2.6 Add tests that starting an episode persists `openai_trace_id`, appends `main_agent_run_id`, sets `latest_main_agent_run_id`, and emits correlated `main_agent.started`.

## 3. Intake Classification Application

- [x] 3.1 Implement classification validation and application helper for `created/intake/unknown` tasks.
- [x] 3.2 Enforce L2+ test requirements during classification application.
- [x] 3.3 Enforce safety-critical elevation to at least L3 with test and formal gates.
- [x] 3.4 Enforce `repair_existing_code` repair-loop requirements.
- [x] 3.5 Persist non-clarification classification as `running/planning` with normalized goal, task type, difficulty profile, gates, and updated timestamp.
- [x] 3.6 Persist clarification-required classification as `waiting_user/clarifying` with open unresolved questions.
- [x] 3.7 Emit `main_agent.decision` and `task.updated` for applied classifications.
- [x] 3.8 Emit `main_agent.clarification_requested` and `task.waiting_user` for clarification pauses.
- [x] 3.9 Add tests that no worker job, worker event, or worker artifact is created before classification is applied.

## 4. Main Agent Service And Runner Boundary

- [x] 4.1 Define `MainAgentService` with a `run_episode(task_id)` entrypoint and dependency injection for session, artifact root, MCP mode, mock scenario, model, max turns, and runner.
- [x] 4.2 Define a runner protocol or adapter boundary that supports fake tests and production OpenAI Agents SDK execution.
- [x] 4.3 Implement production intake agent construction with instructions and `IntakeClassificationOutput`.
- [x] 4.4 Implement production orchestration agent construction with instructions, `get_main_agent_tools()`, `AgentToolContext`, and structured episode output.
- [x] 4.5 Build `RunConfig` with workflow name, trace ID, task ID group ID, and trace metadata.
- [x] 4.6 Route unclassified tasks through intake classification before orchestration.
- [x] 4.7 Route already classified running tasks directly to orchestration.
- [x] 4.8 Skip model execution for terminal tasks without mutating terminal state.
- [x] 4.9 Return or raise not-found failure for missing tasks without side effects.
- [x] 4.10 Handle max-turns and model behavior errors without marking the task `succeeded`.

## 5. Tool-Oriented Orchestration Coverage

- [x] 5.1 Add fake runner support that can execute deterministic tool sequences through `AgentToolService`.
- [x] 5.2 Add integration test for ordinary L2 development: classify, call `plc-dev`, call `plc-test`, run Quality Gate, finish succeeded.
- [x] 5.3 Add integration test for L3 safety-critical development: classify, call `plc-dev`, call `plc-test` and `plc-formal`, run Quality Gate, finish succeeded.
- [x] 5.4 Add integration test for test failure repair: classify, dev, failed test, repair, regression test, Quality Gate, finish succeeded.
- [x] 5.5 Add integration test for formal failure repair: classify, dev, test pass, formal fail, repair, regression test, formal regression, Quality Gate, finish succeeded.
- [x] 5.6 Add integration test for clarification-required task: classify to waiting user and create no worker jobs.
- [x] 5.7 Assert worker inputs inherit `openai_trace_id` and latest `main_agent_run_id` from task trace in at least one orchestration test.
- [x] 5.8 Assert episode outputs include task ID, main agent run ID, final task status, decisions, plan summary, artifact references, gate summary, and next recommended action.

## 6. Configuration, Scripts, And Verification

- [x] 6.1 Decide whether to add `MAIN_AGENT_MODEL` and `MAIN_AGENT_MAX_TURNS` to `Settings`; implement the chosen minimal configuration path.
- [x] 6.2 Add a local script or focused test helper for invoking `MainAgentService.run_episode` against mock workers without requiring live OpenAI calls.
- [x] 6.3 Run `uv run pytest backend/app/tests/unit/test_main_agent*.py -q` or the equivalent focused unit target.
- [x] 6.4 Run `uv run pytest backend/app/tests/integration/test_main_agent_with_mock_tools.py -q` or the equivalent focused integration target.
- [x] 6.5 Run `uv run pytest backend/app/tests/unit/test_agent_tools.py backend/app/tests/unit/test_task_service.py -q` to verify existing tool and task behavior still passes.
- [x] 6.6 Run `uv run python -m compileall backend`.
- [x] 6.7 Run `git diff --check`.
