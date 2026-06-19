## Why

Router already has focused unit, integration, and mock E2E coverage, but it does not yet have a fixed PLC task evaluation set that tracks end-to-end behavior across representative user requests. Prompt, runner, worker, or policy changes can regress routing decisions, required validation steps, repair loops, reports, or audit trails without a single stable regression signal.

## What Changes

- Add a backend evaluation suite driven by a fixed YAML task set under `backend/app/tests/eval/`.
- Define at least 15 representative PLC evaluation cases covering QA, new development, safety-critical formal verification, clarification, repair, repair exhaustion, modification, timeout/error handling, and final-report evidence.
- Add a deterministic mock eval harness that creates tasks through the normal task path, runs Runtime through the existing Main Agent runner boundary with scripted classifications/tool plans, and audits persisted task state, worker jobs, artifacts, gate results, events, and final reports.
- Generate a compact `eval_report.md` summarizing case results, expected worker paths, final statuses, and key invariant failures.
- Keep live model/provider evaluation opt-in and separate from the default deterministic eval so CI remains stable and does not require network calls or API keys.
- Add local developer entry points for `make eval` or an equivalent documented command once deployment tooling is present.

## Capabilities

### New Capabilities

- `backend-eval-suite`: Defines the fixed backend PLC task evaluation set, deterministic eval runner behavior, reports, and regression invariants for Router task orchestration.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `backend/app/tests/eval/plc_tasks.yaml`
  - `backend/app/tests/eval/test_eval_tasks.py`
  - optional eval helpers under `backend/app/tests/eval/`
  - optional generated `eval_report.md`
  - optional `Makefile` target or documentation for running evals
- Existing public HTTP APIs remain unchanged.
- Existing Router schemas, JSON Schema files, TypeScript declarations, and database tables remain unchanged.
- Default eval execution remains deterministic and offline by using mock workers and scripted Main Agent outputs.
- Optional live/provider eval may use configured OpenAI-compatible or official OpenAI provider settings but must be explicitly enabled.
