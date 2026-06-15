## 1. Schema Export

- [x] 1.1 Implement `backend/app/schemas/json_schema_export.py` to export schemas from `ROUTER_V1_SCHEMA_MODELS`.
- [x] 1.2 Add stable JSON Schema metadata for each export, including dialect, schema id, and `x-schema-version`.
- [x] 1.3 Export the documented `schema/router_event.schema.json` file for `RouterEvent`.
- [x] 1.4 Preserve or regenerate `schema/event.schema.json` as a compatibility alias if required by existing repository references.

## 2. Fixtures

- [x] 2.1 Add `backend/app/tests/fixtures/task_state.valid.json`.
- [x] 2.2 Add `backend/app/tests/fixtures/worker_input.plc_dev.valid.json`.
- [x] 2.3 Add `backend/app/tests/fixtures/worker_result.test_failed.valid.json` with `execution_status` distinct from `outcome.status`.
- [x] 2.4 Add `backend/app/tests/fixtures/artifact.plc_code.valid.json` without requiring `inline_content`.
- [x] 2.5 Add `backend/app/tests/fixtures/event.worker_started.valid.json`.

## 3. Unit Tests

- [x] 3.1 Add `backend/app/tests/unit/test_router_schema.py` covering valid `TaskState` parsing.
- [x] 3.2 Test that invalid `schema_version` values are rejected.
- [x] 3.3 Test that `WorkerInput` without `task_id` is rejected.
- [x] 3.4 Test that `WorkerResult.execution_status` and `WorkerResult.outcome.status` are validated and preserved separately.
- [x] 3.5 Test that `Artifact` validates when large content is externalized and `inline_content` is omitted.
- [x] 3.6 Test that `RouterEvent.seq` must be an integer.

## 4. Fixture and Export Verification

- [x] 4.1 Add `backend/app/tests/unit/test_schema_fixtures.py` to parse every valid fixture with its corresponding Pydantic model.
- [x] 4.2 Add coverage proving the JSON Schema export command writes all required schema files.
- [x] 4.3 Add or document a lightweight check that the TypeScript contract remains aligned with the Pydantic and JSON Schema field surface.

## 5. Validation

- [x] 5.1 Run `uv run python -m compileall backend`.
- [x] 5.2 Run `uv run pytest backend/app/tests/unit/test_router_schema.py -q`.
- [x] 5.3 Run `uv run pytest backend/app/tests/unit/test_schema_fixtures.py -q`.
- [x] 5.4 Run `cd backend && uv run python -m app.schemas.json_schema_export`.
- [x] 5.5 Run `git diff --check`.
