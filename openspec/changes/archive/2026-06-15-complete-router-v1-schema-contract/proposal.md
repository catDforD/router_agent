## Why

`docs/backend.md` defines Router v1's five core backend schemas as a contract boundary, but the repository only has partial implementation evidence today. The Pydantic models exist, yet JSON Schema export is not implemented, fixtures are missing, and the documented validation tests do not exist, leaving cross-service contract drift easy to miss.

## What Changes

- Complete the Router v1 schema contract delivery loop around `TaskState`, `WorkerInput`, `WorkerResult`, `Artifact`, and `RouterEvent`.
- Add a repeatable JSON Schema export path from the Pydantic source models, including stable schema metadata.
- Add representative valid JSON fixtures for each top-level schema.
- Add focused unit tests for the validation behaviors called out in `docs/backend.md`.
- Add fixture parsing tests so committed examples remain compatible with the Pydantic contract.
- Clarify the router event schema file naming so generated JSON Schema and documented paths do not diverge silently.

## Capabilities

### New Capabilities
- `router-v1-schema-contract`: Covers Router v1 backend schema validation, JSON Schema export, fixture parsing, and contract consistency checks for the five core cross-service schemas.

### Modified Capabilities

None.

## Impact

- Affected backend model and schema files: `backend/app/models/router_schema.py`, `backend/app/schemas/json_schema_export.py`, and `schema/*.schema.json`.
- Affected tests and fixtures: `backend/app/tests/unit/` and `backend/app/tests/fixtures/`.
- Affected consumer contract reference: `schema/ts/router_contract.d.ts` may need alignment checks if implementation uncovers drift.
- No runtime API behavior or database schema changes are intended.
