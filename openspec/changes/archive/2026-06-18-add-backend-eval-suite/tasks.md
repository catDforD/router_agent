## 1. Eval Corpus

- [x] 1.1 Create `backend/app/tests/eval/` with a focused eval case loader module.
- [x] 1.2 Define typed eval case models or validation helpers for case IDs, messages, project context, scripted classification, scripted sequence, mock scenario, expected workers, expected artifacts, expected events, final status policy, and named invariants.
- [x] 1.3 Add `backend/app/tests/eval/plc_tasks.yaml` with at least 15 representative PLC cases covering the required workflow matrix.
- [x] 1.4 Validate task types, difficulty levels, worker types, artifact types, event types, final statuses, mock scenarios, and invariant names against Router contract values before cases execute.
- [x] 1.5 Add loader tests or eval harness checks proving duplicate case IDs, invalid enum values, and missing required fields fail with clear diagnostics.

## 2. Deterministic Eval Harness

- [x] 2.1 Add `backend/app/tests/eval/test_eval_tasks.py` with pytest parametrization over the YAML corpus.
- [x] 2.2 Reuse the existing SQLite test database and local artifact root pattern from mock E2E tests for isolated eval case execution.
- [x] 2.3 Implement a scripted eval runner that returns the YAML-provided intake classification and executes the YAML-provided tool sequence through `AgentToolService`.
- [x] 2.4 Start each deterministic eval case through the Task API path or equivalent TaskService creation path and verify raw request artifact plus `task.created` event before Runtime execution.
- [x] 2.5 Run each case through `RuntimeService.start_task` using the configured mock worker scenario and normal Main Agent service boundary.
- [x] 2.6 Support clarification cases that stop after intake without creating worker jobs.
- [x] 2.7 Support worker timeout or worker error cases without accepting false terminal success.

## 3. Assertions and Invariants

- [x] 3.1 Add an eval audit snapshot helper that loads persisted `TaskState`, worker jobs, artifacts, visible events, gate results, and final report content after each case.
- [x] 3.2 Assert expected final status policy, minimum difficulty, required workers, forbidden workers, required artifacts, artifact versions when specified, and required event subsequences.
- [x] 3.3 Implement named invariant assertions for `l3_requires_formal`, `repair_requires_regression`, `formal_repair_requires_formal_regression`, `no_success_without_quality_gate`, `final_report_before_terminal_event`, `no_worker_for_clarification`, `no_fourth_repair_round`, and `no_false_success_on_worker_error`.
- [x] 3.4 Assert final report artifacts reference current delivery artifacts and do not embed full PLC code, test reports, formal reports, counterexamples, patches, worker logs, or Main Agent replay logs.
- [x] 3.5 Ensure failed eval assertions include case ID, task ID, actual status, actual worker sequence, and failing invariant name when applicable.

## 4. Eval Report

- [x] 4.1 Add a compact eval result record for each case containing case ID, pass/fail result, task ID, expected final status policy, actual final status, worker sequence, artifact summary, invariant outcomes, and bounded failure reason.
- [x] 4.2 Generate `eval_report.md` or a configurable report path after eval execution.
- [x] 4.3 Keep the Markdown report compact and exclude full large artifact contents.
- [x] 4.4 Add test coverage or assertions proving the report includes every executed case and useful failure diagnostics.

## 5. Optional Live Provider Eval Hook

- [x] 5.1 Add skipped or deselected live/provider eval scaffolding that is disabled unless an explicit environment flag or pytest option is set.
- [x] 5.2 Reuse the fixed YAML case corpus for live/provider eval where practical while applying broader required/forbidden worker and invariant assertions instead of exact scripted sequences.
- [x] 5.3 Ensure live/provider eval skips cleanly when required provider configuration is absent and never runs as part of the default deterministic eval.

## 6. Developer Entry Point and Verification

- [x] 6.1 Add or document the eval command, preferring `uv run pytest backend/app/tests/eval/test_eval_tasks.py -q` and adding `make eval` only if a Makefile is introduced for deployment tooling.
- [x] 6.2 Run `uv run pytest backend/app/tests/eval/test_eval_tasks.py -q`.
- [x] 6.3 Run existing mock E2E coverage with `uv run pytest backend/app/tests/e2e/test_router_mock_scenarios.py -q`.
- [x] 6.4 Run focused guard and gate coverage with `uv run pytest backend/app/tests/unit/test_scheduler_guard.py backend/app/tests/unit/test_quality_gate.py -q`.
- [x] 6.5 Run `uv run python -m compileall backend`.
- [ ] 6.6 Run `git diff --check`.
