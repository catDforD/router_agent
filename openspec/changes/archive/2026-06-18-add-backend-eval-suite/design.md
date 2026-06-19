## Context

`docs/backend.md` step 27 calls for a backend eval set that regresses Router behavior over a fixed PLC task corpus. The repository already has strong lower-level coverage: schema tests, repository tests, Scheduler Guard tests, Quality Gate tests, MCP mock/real adapter tests, Main Agent service tests, Runtime loop tests, and deterministic mock E2E scenarios. What is missing is a task-level suite that makes representative user requests reusable as regression cases.

The current deterministic E2E tests already exercise the right production boundaries: task creation, Runtime, Main Agent runner protocol, function tools, mock MCP adapter, WorkerResult projection, Quality Gate, final report generation, events, artifacts, and worker job persistence. The eval suite should reuse that shape instead of introducing a parallel runtime path.

There is also an active provider-compatibility change for OpenAI-compatible Main Agent runners. The eval suite should not depend on that work for its first implementation. It should provide an offline deterministic baseline first, then allow optional live/provider eval once runner behavior is stable enough to compare against the same case file.

## Goals / Non-Goals

**Goals:**

- Provide a fixed YAML corpus with at least 15 representative PLC backend eval cases.
- Run deterministic offline evals by default without `OPENAI_API_KEY`, external network access, or real MCP workers.
- Exercise the normal task/runtime/tool/worker/gate/report persistence boundaries.
- Assert behavior through stable Router contracts: task state, worker job sequence, artifacts, events, gate results, final report references, and policy invariants.
- Emit a compact Markdown eval report useful for local review and CI artifacts.
- Leave room for opt-in live/provider eval against the same task definitions without making that path part of default CI.

**Non-Goals:**

- Do not evaluate PLC code semantic correctness beyond the existing mock worker and artifact contracts.
- Do not replace unit, integration, or mock E2E tests.
- Do not require real OpenAI, OpenAI-compatible, DeepSeek, or MCP provider calls in default eval runs.
- Do not change public HTTP APIs, Router schemas, database schema, or TypeScript contracts.
- Do not implement the OpenAI-compatible Main Agent runner as part of this change.

## Decisions

### Use a YAML task corpus as the eval source of truth

Each case should live in `backend/app/tests/eval/plc_tasks.yaml` with a stable `id`, user `message`, optional `project_context`, deterministic runner fields, and expected assertions.

The YAML file should contain contract-like values rather than prose-only notes. Examples include `task_type`, `difficulty_level`, `mock_scenario`, `scripted_sequence`, `required_workers`, `required_artifacts`, accepted `final_status` values, and named invariants.

Alternative considered: encode all cases directly in pytest parametrization. Rejected because it makes the task set harder to review, reuse for live eval, and summarize in reports.

### Make deterministic mock eval the default path

Default eval should use the same style as the existing scripted mock E2E runner: scripted intake classification, scripted orchestration action sequence, configured mock worker scenario, and normal `RuntimeService.start_task` execution.

This keeps eval stable enough for CI while still testing the Router runtime surfaces that matter for regressions.

Alternative considered: use a live model for every eval. Rejected because model nondeterminism, provider outages, cost, latency, and active runner compatibility work would make the first eval suite noisy.

### Assert persisted audit surfaces, not incidental in-memory outputs

Eval assertions should load persisted state after each case and inspect:

- final `TaskState`
- `worker_jobs`
- artifact rows and selected artifact contents
- visible event ordering
- gate result rows
- final report payload

This matches the product requirement that any task can be replayed from task ID, events, artifacts, worker jobs, and gates.

Alternative considered: assert only returned runtime output. Rejected because it would miss the main user-facing and debugging surfaces.

### Use named invariants for policy checks

The harness should support reusable invariant names such as:

- `l3_requires_formal`
- `repair_requires_regression`
- `formal_repair_requires_formal_regression`
- `no_success_without_quality_gate`
- `final_report_before_terminal_event`
- `no_worker_for_clarification`
- `no_fourth_repair_round`
- `no_false_success_on_worker_error`

Named invariants make YAML concise and keep policy logic centralized in test helpers.

Alternative considered: repeat every assertion field in every case. Rejected because it increases drift and makes future policy updates harder.

### Keep live/provider eval opt-in and comparable

The initial file format should allow a future `eval_mode: live_provider` or marker-based pytest path to use the same user messages and expected broad invariants while allowing looser assertions for model-selected tool sequences.

Live/provider eval should be skipped unless an explicit environment variable or pytest option enables it. The default `make eval` path should remain deterministic.

Alternative considered: create a separate live eval corpus. Rejected for the first version because the same representative task set is more useful if deterministic and live modes can be compared.

### Generate a Markdown report as a test artifact

The eval harness should write `eval_report.md` or a path supplied by environment/config. The report should summarize case ID, status, expected vs actual final status, worker sequence, artifact summary, invariant results, and failure reason when applicable.

The report should not embed full PLC code, test reports, formal reports, patches, worker logs, or replay logs.

Alternative considered: print pytest output only. Rejected because a compact report is easier to inspect in CI and local reviews.

## Risks / Trade-offs

- [Risk] Deterministic eval can miss prompt regressions. -> Mitigation: treat deterministic eval as the required baseline and add opt-in live/provider eval once runner behavior is stable.
- [Risk] YAML case fields can drift from Pydantic enum values. -> Mitigation: validate eval case data through typed helper models or direct enum/value checks before running cases.
- [Risk] Eval can duplicate existing mock E2E logic. -> Mitigation: extract small shared helpers or keep eval helpers focused on parameterized case loading and reusable invariants.
- [Risk] Reports can become noisy or contain large generated content. -> Mitigation: report only summaries, IDs, artifact metadata, invariant outcomes, and bounded error details.
- [Risk] Overly strict worker sequence assertions can reject valid live model behavior. -> Mitigation: keep deterministic eval strict and live/provider eval broad, using required/forbidden workers and invariants instead of exact sequences where needed.

## Migration Plan

No database or public API migration is required.

Implementation can be rolled out in small steps:

1. Add the eval YAML corpus and loader validation.
2. Add deterministic eval pytest harness that reuses existing Runtime/mock worker paths.
3. Add reusable invariant assertions and final report checks.
4. Add Markdown report generation.
5. Add or document `make eval` once the repository deployment tooling is ready.
6. Optionally add skipped live/provider eval hooks after the compatible runner change is available.

Rollback removes the eval test directory and optional Makefile/doc entry without affecting runtime behavior.

## Open Questions

- Should the generated `eval_report.md` be written to the repository root, a temporary pytest path, or a configurable artifact path by default?
- Should `make eval` be introduced in this change if no Makefile exists yet, or should the first implementation document the `uv run pytest backend/app/tests/eval/test_eval_tasks.py -q` command and leave Makefile creation to deployment tooling?
- Should live/provider eval be included as skipped scaffolding in the first implementation, or deferred until the OpenAI-compatible runner change lands?
