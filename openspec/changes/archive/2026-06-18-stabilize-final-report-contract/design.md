## Context

The backend already writes `FINAL_REPORT` and `MAIN_AGENT_LOG` artifacts during report-first Main Agent finalization. The current `FINAL_REPORT` content is a JSON wrapper around `MainAgentEpisodeOutput`, which proves report durability but leaves the user-facing delivery contract under-specified.

`docs/backend.md` section 26 still describes the older idea that `finish_task` must generate `final_report:v1`. The implementation and current specs have moved away from that shape: orchestration should return a final structured output, Runtime should persist report artifacts, and Runtime should only then apply terminal task status.

The frontend and audit trail need a stable report object that answers "what was delivered, what evidence supports it, and what remains unresolved" without embedding large PLC code, logs, test output, formal reports, patches, or replay details.

## Goals / Non-Goals

**Goals:**

- Define a stable `FINAL_REPORT` payload shape for Router v1 terminal delivery reports.
- Build the report from persisted Router state, not only from model-supplied final output.
- Ensure `succeeded`, `partial_failed`, and `failed` outcomes finalized through Main Agent/Runtime have a user-visible report before their terminal task event.
- Keep report content compact and artifact-oriented.
- Preserve current HTTP APIs, database tables, artifact enum values, and report-first finalization architecture.
- Update tests so they verify report content and source references, not only report existence.

**Non-Goals:**

- Do not add a new artifact type or Router v2 schema.
- Do not embed full artifact contents in the final report.
- Do not require a final report for `waiting_user` pauses.
- Do not require a final report for user-initiated `cancelled` tasks in this change.
- Do not implement the OpenAI-compatible Main Agent runner; it should later reuse this same report path.
- Do not build frontend rendering logic.

## Decisions

### Build reports from persisted state

Introduce or isolate a final report builder that composes report content from:

- `TaskState`
- `MainAgentEpisodeOutput`
- current artifact references
- persisted gate results
- failures, assumptions, unresolved questions, and runtime limits
- trace summary references when available

The builder should produce a compact JSON-compatible object. `MainAgentEpisodeOutput` remains an input, but it is not the sole source of truth.

Alternative considered: keep storing only `MainAgentEpisodeOutput`. Rejected because the model can omit important persisted facts such as the latest gate report, a repaired code version, unresolved blocking failures, or exact artifact IDs.

### Use a stable report payload instead of markdown-only output

The report artifact should remain machine-readable JSON with a stable version marker such as `report_version: 1`. It can include frontend-friendly section fields, but generated prose should not be the only representation.

Recommended top-level shape:

```text
kind: main_agent_final_report
schema_version: router.v1
report_version: 1
created_at
task_id
main_agent_run_id
final_task_status
user_goal
classification
delivery_artifacts
validation_summary
repair_summary
assumptions
unresolved_items
gate_summary
trace_refs
main_agent_output_summary
```

Alternative considered: write `final_report_v1.md`. Rejected for the backend contract because markdown is useful for display but weak for deterministic tests and frontend extraction. A derived markdown view can be added later if needed.

### Keep report-first finalization as the authoritative path

The primary path stays:

```text
validated final output
  -> build FINAL_REPORT content
  -> write FINAL_REPORT artifact
  -> write MAIN_AGENT_LOG artifact
  -> emit main_agent.completed
  -> apply task.succeeded / task.partial_failed / task.failed
```

Direct `finish_task` remains guarded for developer or legacy invocation, but model orchestration should not rely on it for successful finalization.

Alternative considered: move report writing into `finish_task`. Rejected because it can reintroduce the earlier race where a terminal task is marked before final output validation and report persistence are complete.

### Treat failed finalization as reportable when Runtime intentionally terminalizes

If Runtime intentionally terminalizes a task as `failed` because of an unrecoverable Main Agent control-plane failure, such as max turns, it should write a deterministic failure report first. The report should explain the error code, preserve available artifacts and gate state, and avoid claiming successful delivery.

Model output that is malformed or schema-invalid should not be treated as a valid final delivery recommendation. It should remain observable, and if Runtime chooses to terminalize after an unrecoverable error, the deterministic failure report path should be used.

Alternative considered: only generate reports for success and partial failure. Rejected because frontend users still need a final explanation and artifact references when the task is marked failed.

### Keep reports compact and content-free

The final report should reference large outputs by artifact ID, type, version, summary, URI, and hash. It should not inline PLC code, full test logs, formal report bodies, counterexamples, patches, replay logs, raw model outputs, raw MCP payloads, or hidden reasoning.

Alternative considered: include bounded artifact excerpts. Deferred because the current artifact API and `read_artifact` tool already support bounded reads, and reports should remain stable and small.

## Risks / Trade-offs

- [Risk] The report builder can duplicate state already present in `TaskState` and trace summaries. -> Mitigation: use compact references and summaries; treat persisted rows as source data, not copied large content.
- [Risk] Terminal failed report generation can itself fail. -> Mitigation: do not emit the terminal task event until report persistence succeeds; record an observable error and leave the task non-successful if report persistence fails.
- [Risk] Frontend may prefer markdown. -> Mitigation: keep the authoritative artifact JSON and optionally add a display section or derived renderer later.
- [Risk] Existing tests that call `finish_task` directly may not get final reports. -> Mitigation: keep direct tool semantics scoped as guarded legacy/dev behavior and add report assertions around Main Agent/Runtime finalization paths.
- [Risk] The in-progress compatible provider runner may produce sparse final JSON. -> Mitigation: build report content from persisted Router state so provider output only needs to validate the final recommendation and summary.

## Migration Plan

No database, HTTP API, or schema migration is required.

Implementation can proceed in small steps:

1. Add report payload builder tests against representative `TaskState` and persisted artifact/gate data.
2. Replace the current final report content wrapper with the stable report payload while preserving artifact type, visibility, creator, and metadata.
3. Route `succeeded`, `partial_failed`, and valid `failed` final outputs through the same report-first persistence path.
4. Add deterministic failure report generation for unrecoverable Main Agent control-plane terminalization.
5. Extend integration and E2E tests to assert report content, artifact references, and event ordering.
6. Update `docs/backend.md` section 26 to match report-first finalization.

Rollback can restore the previous `MainAgentEpisodeOutput` wrapper content while keeping existing artifact IDs and API surfaces compatible.

## Open Questions

- Should the JSON report include a pre-rendered `display_markdown` field in v1, or should frontend derive display sections from structured fields?
- Should user-initiated `cancelled` tasks get a lightweight cancellation summary artifact in a later change?
- Should `GET /api/tasks/{task_id}/trace` expose only the final report artifact ID for user-facing trace summaries, or also the internal replay log ID when referenced by `main_agent.completed`?
