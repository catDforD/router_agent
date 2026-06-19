## 1. Final Report Payload Builder

- [x] 1.1 Define a stable report payload builder for `FINAL_REPORT` content using persisted `TaskState`, `MainAgentEpisodeOutput`, artifact refs, failures, assumptions, unresolved questions, gate state, runtime limits, and trace refs.
- [x] 1.2 Include `kind`, `schema_version`, `report_version`, `created_at`, `task_id`, `main_agent_run_id`, `final_task_status`, user goal, classification, delivery artifacts, validation summary, repair summary, assumptions, unresolved items, gate summary, trace refs, and Main Agent summary fields.
- [x] 1.3 Keep report output compact by storing artifact IDs, types, versions, summaries, URIs, and hashes without embedding full PLC code, logs, reports, counterexamples, patches, raw model output, raw MCP payloads, or hidden reasoning.
- [x] 1.4 Add focused unit tests for successful delivery, repaired delivery, partial failure, and missing optional artifacts.

## 2. Report-First Finalization Paths

- [x] 2.1 Replace the current `MainAgentEpisodeOutput` wrapper report content with the stable report payload while preserving `FINAL_REPORT` artifact type, user visibility, creator metadata, MIME type, and task artifact pointer updates.
- [x] 2.2 Ensure valid `succeeded`, `partial_failed`, and valid `failed` Main Agent final outputs write `FINAL_REPORT` and `MAIN_AGENT_LOG`, emit `main_agent.completed`, and only then emit the matching terminal task event.
- [x] 2.3 Add deterministic failure report generation for unrecoverable Main Agent control-plane terminalization, including `MAIN_AGENT_MAX_TURNS_EXCEEDED`.
- [x] 2.4 Preserve existing invalid final output behavior so malformed or schema-invalid final output cannot mark a task `succeeded`.
- [x] 2.5 Preserve direct `finish_task` guard behavior while keeping model orchestration on the report-first structured output path.

## 3. Artifact and API Coverage

- [x] 3.1 Add artifact store tests proving `FINAL_REPORT` content includes `report_version: 1` and the required structured sections.
- [x] 3.2 Add artifact API tests proving final report JSON is readable as UTF-8 content and remains user-visible.
- [x] 3.3 Add negative content tests proving final reports do not inline full code, full test report bodies, formal reports, patches, counterexamples, replay logs, raw model output, raw MCP payloads, or hidden reasoning.
- [x] 3.4 Add trace summary or event correlation assertions proving `main_agent.completed` references the final report artifact ID.

## 4. Integration and E2E Coverage

- [x] 4.1 Extend Main Agent mock integration tests to assert final report content for a `succeeded` dev-test-gate path.
- [x] 4.2 Extend integration tests to assert `partial_failed` finalization writes a report with unresolved blocking failures and available artifact references before `task.partial_failed`.
- [x] 4.3 Extend max-turn or equivalent control-plane failure tests to assert a deterministic failed final report is durable before `task.failed`.
- [x] 4.4 Extend mock E2E success and repair scenarios to verify final PLC code, test or formal report, gate report, patch, and repair summary artifact references in the final report.
- [x] 4.5 Extend repair budget exhaustion E2E coverage to verify final report unresolved items and exhausted repair round count.

## 5. Documentation and Verification

- [x] 5.1 Update `docs/backend.md` section 26 to describe Runtime-owned report-first finalization instead of direct `finish_task` report generation.
- [x] 5.2 Run focused unit tests for final report builder, observability, artifact API, and artifact store behavior.
- [x] 5.3 Run focused integration tests for Main Agent mock tools and timeout/control-plane failure behavior.
- [x] 5.4 Run mock Router E2E scenarios that assert final report content.
- [x] 5.5 Run `uv run python -m compileall backend`.
- [x] 5.6 Run `openspec validate stabilize-final-report-contract`.
- [x] 5.7 Run `git diff --check`.
