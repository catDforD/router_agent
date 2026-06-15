## Context

`docs/backend.md` section 4 requires the Router backend to land five core schemas as real backend Pydantic types, export JSON Schema, provide fixtures, and validate the contract with focused tests. The repository already contains substantial Pydantic definitions in `backend/app/models/router_schema.py` and committed JSON Schema files under `schema/`, but the export script is empty, the required schema tests are missing, and the fixture directory contains no examples.

The current project layout places tests under `backend/app/tests/` with `pythonpath = ["backend"]` in `pyproject.toml`. This change should follow that existing layout instead of introducing a parallel root-level `tests/` tree.

## Goals / Non-Goals

**Goals:**

- Treat `backend/app/models/router_schema.py` as the backend validation source of truth for Router v1.
- Make JSON Schema export repeatable through `python -m app.schemas.json_schema_export` from the `backend/` directory.
- Keep exported JSON Schema files stable enough for external consumers by including schema metadata such as `$schema`, `$id`, and `x-schema-version`.
- Add representative fixtures for the five top-level schemas and keep them parseable by Pydantic.
- Add unit tests for the validation behaviors explicitly listed in `docs/backend.md`.
- Resolve the event schema naming mismatch without unexpectedly removing an existing committed schema path.

**Non-Goals:**

- Redesign the Router v1 schema shape beyond fixes required to pass the documented contract checks.
- Add database persistence, repository behavior, artifact storage, worker invocation, or FastAPI endpoints.
- Generate TypeScript declarations automatically.
- Introduce new runtime dependencies beyond the existing Python/Pydantic test stack unless a small dev-only validation dependency becomes necessary.

## Decisions

1. Pydantic models remain the backend schema source of truth.

   The export script should import `ROUTER_V1_SCHEMA_MODELS` and call each model's JSON Schema generation rather than maintaining hand-written JSON Schema. This keeps backend validation and exported contracts aligned. The alternative is manually editing `schema/*.schema.json`, but that makes drift likely and provides no repeatable verification path.

2. The export script adds stable top-level metadata around Pydantic output.

   Existing schema files include `$schema`, `$id`, and `x-schema-version`; direct `model_json_schema()` output does not. The exporter should preserve that metadata convention while leaving the generated Pydantic schema body intact. This supports downstream tooling while keeping diff noise low.

3. Router event schema naming should support the documented name and existing path.

   `docs/backend.md` names `schema/router_event.schema.json`, while the repository currently has `schema/event.schema.json` generated from `RouterEvent`. The implementation should write `router_event.schema.json` as the documented canonical export. It may also retain or regenerate `event.schema.json` as a compatibility alias if existing consumers depend on it.

4. Fixtures should be minimal but semantically representative.

   Each fixture should include the fields required by the current Pydantic models and exercise the scenario implied by its filename. For example, `worker_result.test_failed.valid.json` should have successful tool execution with a failed business outcome, proving `execution_status` and `outcome.status` are separate concepts.

5. Tests should focus on contract behavior, not implementation internals.

   Unit tests should instantiate Pydantic models and assert validation success or failure for documented behaviors. Fixture tests should load JSON files and call the corresponding model validators. Export tests should verify files can be generated and match the expected model set and metadata shape.

## Risks / Trade-offs

- [Risk] Existing external consumers may already reference `schema/event.schema.json`. -> Mitigation: keep a compatibility alias during implementation unless maintainers explicitly choose a breaking cleanup.
- [Risk] Fixtures can become large and hard to maintain because the schemas are nested. -> Mitigation: keep fixtures minimal, prefer empty arrays/nullable optional fields where valid, and only include realistic detail that supports the named scenario.
- [Risk] Direct JSON Schema equality tests may fail due to harmless formatting or metadata order. -> Mitigation: compare parsed JSON objects and normalize the known top-level metadata distinction.
- [Risk] TypeScript contract drift may still occur because generation is out of scope. -> Mitigation: add an explicit lightweight check or documented manual verification point in tests/tasks, and leave automatic TS generation for a later change.

## Migration Plan

This is a development-time contract completion change. No runtime migration is required.

1. Add or update schema export tooling and tests.
2. Generate the documented JSON Schema files.
3. Add fixtures and fixture parsing tests.
4. Run the documented validation commands and `git diff --check`.

Rollback is limited to reverting the OpenSpec implementation commit; no persisted data shape changes are introduced.

## Open Questions

- Should `schema/event.schema.json` remain permanently as an alias, or should consumers migrate to `schema/router_event.schema.json` after this change?
- Should TypeScript declarations remain hand-maintained for now, or should a future change introduce generated TypeScript from JSON Schema?
