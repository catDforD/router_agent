## Context

The backend already persists Router v1 `TaskState`, `Artifact`, `RouterEvent`, `WorkerInput`, `WorkerResult`, worker jobs, and gate results. It also has a local Artifact Store, append-only Event Service, Scheduler Guard, and Quality Gate with unit coverage. The MCP layer, worker input builder, worker result handler, runtime loop, and agent tools are still empty.

Step 12 in `docs/backend.md` needs the worker protocol to run without real subagents. This change should prove the MCP boundary and audit trail first: a valid `WorkerInput` is executed through a mock worker, mock artifact content is persisted, a standard `WorkerResult` is returned, and worker/artifact events plus worker job rows can be replayed.

One important constraint is that `WorkerResult.produced_artifacts` contains `ArtifactRef` objects, not artifact content. Therefore mock workers can describe content to produce, but the backend adapter must persist that content through `ArtifactStore` before finalizing the `WorkerResult`.

## Goals / Non-Goals

**Goals:**

- Execute `plc-dev`, `plc-test`, `plc-formal`, and `plc-repair` in `MCP_MODE=mock`.
- Support deterministic mock scenarios: `dev_test_pass`, `test_failed_then_repair_pass`, `formal_failed_then_repair_pass`, `need_clarification`, and `worker_timeout`.
- Persist mock-produced artifacts through the existing local Artifact Store and return `ArtifactRef` references in `WorkerResult`.
- Persist worker job lifecycle records and append user-visible worker/artifact events.
- Normalize timeout, schema-invalid, and execution-error paths into valid Router v1 `WorkerResult` payloads.
- Add focused unit tests and a direct development script for mock worker calls.

**Non-Goals:**

- Do not connect to real MCP servers or add real MCP SDK/network dependencies.
- Do not implement `worker_input_builder.py`, `worker_result_handler.py`, Main Agent function tools, or the runtime loop.
- Do not update TaskState gates, failures, repair rounds, active worker refs, or completed worker refs beyond what existing Artifact Store pointer updates already do.
- Do not change Router v1 schema fields or JSON Schema contracts.

## Decisions

### Adapter owns the worker execution envelope

Add an adapter service that accepts an already validated `WorkerInput` plus runtime dependencies (`Session`, artifact root, scenario/settings) and owns the common side effects:

```text
WorkerInput
  -> create worker_jobs row
  -> append worker.started
  -> call mock worker
  -> write produced artifact content
  -> build/normalize WorkerResult
  -> complete worker_jobs row
  -> append artifact.created and worker.completed/error/timeout
```

This centralizes the audit trail. Later Agent tools can call this adapter service instead of reimplementing worker job and event mechanics in each tool. The alternative is letting Agent tools create jobs/events and using the adapter only for raw MCP calls, but that would duplicate the same lifecycle logic in direct scripts, tests, and tools.

### Mock workers return artifact write intents, not persisted refs

`mock_worker.py` should produce a small internal result shape containing outcome data, diagnostics/failures/clarifications, metrics, and artifact write intents. Each artifact write intent includes type, version, name, content, summary, metadata, visibility, and parent artifact IDs. The adapter writes those intents with `ArtifactStore.write_artifact_content()` and converts the persisted artifacts to `ArtifactRef`.

The alternative is for mock workers to call `ArtifactStore` directly, but that would make mock behavior depend on persistence services and would make later real MCP response normalization harder to share.

### `plc-dev` produces requirements as well as code

The existing `WorkerInput` and Scheduler Guard require `plc-test` and `plc-formal` inputs to include both `requirements_ir` and `plc_code`. To make a mock happy path usable after `plc-dev`, the mock `plc-dev` should produce at least `requirements_ir:v1` and `plc_code:v1`, and may also produce `io_contract:v1`.

The alternative is to require a separate requirements worker before `plc-dev`, but no such worker exists in the current Router v1 worker set.

### Scenario behavior is deterministic from input state

Mock scenario behavior should avoid hidden global counters. For failure-then-repair flows, behavior can be derived from worker type and artifact version:

- `test_failed_then_repair_pass`: `plc-test` fails for `plc_code:v1` and passes for `plc_code:v2+`.
- `formal_failed_then_repair_pass`: `plc-formal` fails for `plc_code:v1` and passes for `plc_code:v2+`.
- `plc-repair` produces `patch:vN`, `repair_summary:vN`, and `plc_code:vN+1` based on the current code artifact version.

This keeps unit tests stable and avoids sharing mutable scenario state across test cases.

### Timeout is normalized without real sleeps

The `worker_timeout` scenario should exercise adapter normalization directly instead of sleeping until a timeout. The adapter can catch a mock timeout exception and return a valid `WorkerResult` with `execution_status="timeout"`, `outcome.status="unknown"`, a retryable `WorkerError`, no produced artifacts, and `next_recommended_action="retry"`.

This makes timeout tests fast and deterministic. Real wall-clock timeout behavior belongs to the later real MCP integration and reliability changes.

### Worker and artifact events are frontend-visible summaries

`worker.started`, `artifact.created`, `worker.completed`, `worker.error`, and `worker.timeout` events produced by the adapter should use `visibility="user"` with compact payloads. Internal details such as full worker input, raw logs, or raw MCP errors should stay in worker job rows or internal metadata, not visible event payloads.

This matches the current SSE service behavior, which hides internal events by default, and lets the frontend timeline observe the mock happy path.

## Risks / Trade-offs

- [Risk] Adapter lifecycle ownership may differ from the step 14 outline where Agent tools create worker jobs and events. -> Mitigation: treat this adapter as the reusable worker execution service that Agent tools call, keeping the single audit path.
- [Risk] Mock outputs could accidentally become too realistic or too tied to future real worker implementation. -> Mitigation: keep mock payloads small, deterministic, and contract-focused.
- [Risk] Artifact Store updates current artifact pointers, but this change does not update gates/failures/repair rounds. -> Mitigation: leave semantic TaskState updates to the WorkerResult Handler change and assert only artifact/job/event/result behavior here.
- [Risk] `need_clarification` from a worker is not enough to pause a task without result handling. -> Mitigation: return a valid `clarification_request` in `WorkerResult`; state transition remains a later handler/runtime responsibility.
- [Risk] Tests may need valid classified task state because Scheduler Guard rejects created/intake/unknown tasks. -> Mitigation: adapter unit tests can use direct valid `WorkerInput` fixtures and classified task fixtures rather than starting from `POST /api/tasks`.

## Migration Plan

No database migration is required. The change only populates existing tables and files through existing repositories and Artifact Store.

Rollback is removing the new MCP adapter/mock/normalizer code, tests, and development script. Existing persisted task, artifact, event, and worker job schemas remain unchanged.

## Open Questions

- Should adapter-created `worker.started` events use source type `mcp_adapter` or `worker`? The current fixture uses `worker`; the adapter can use `worker` for user-visible lifecycle events and reserve `mcp_adapter` for internal normalization/errors.
- Should `MOCK_SCENARIO` be a global setting only, or can scripts/tests pass it directly per adapter invocation? Direct injection is better for unit tests; environment defaults are better for local scripts.
