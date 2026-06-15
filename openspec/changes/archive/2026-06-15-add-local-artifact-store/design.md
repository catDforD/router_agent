## Context

`docs/backend.md` defines Artifact Store as the next backend milestone after database persistence. The repository already has Router v1 artifact schemas, a persisted `artifacts` table, and `ArtifactRepository.create_artifact/get_artifact`, but `backend/app/services/artifact_store.py`, artifact API wiring, and runtime/worker integration points are still empty.

The existing contract already separates metadata from content:

- `Artifact.storage.provider`, `uri`, `path`, `mime_type`, `size_bytes`, and `content_hash` describe externalized content.
- `Artifact.inline_content` is optional and intended only for small JSON or short summaries.
- `ArtifactRef` is the stable shape that future `WorkerInput` construction and Main Agent tools can consume.

This change should therefore add a local content store around the existing Router v1 model rather than redesign the contract.

## Goals / Non-Goals

**Goals:**

- Provide a local filesystem-backed Artifact Store rooted at `Settings.artifact_root`.
- Persist complete Router v1 `Artifact` metadata after writing content.
- Compute and store actual `sha256` content hashes and byte sizes.
- Keep artifact content immutable: new artifacts and versions create new files and rows, never overwrite existing artifact content.
- Return `ArtifactRef` values that can be used directly by future `WorkerInput` construction.
- List artifacts for a task and read artifact content by artifact ID.
- Add read-only FastAPI endpoints for task artifact listing and artifact content retrieval.
- Keep tests focused on service behavior, repository projections, path safety, and API responses.

**Non-Goals:**

- S3, MinIO, database blob storage, or memory providers.
- Upload endpoints or browser-side artifact creation.
- Event/SSE emission for artifact lifecycle events.
- Main Agent tool registration, MCP worker adapter integration, or runtime scheduling.
- Router v1 schema, JSON Schema, or TypeScript contract changes.
- Artifact deletion, mutation, garbage collection, or archival policy.

## Decisions

1. Use a service boundary for content plus metadata.

   `ArtifactStore` should coordinate filesystem writes, content metadata calculation, Router v1 `Artifact` construction, repository persistence, and `ArtifactRef` creation. The lower-level repository should remain metadata-only.

   Alternative considered: add file writes directly to `ArtifactRepository`. That would mix storage provider behavior into persistence code and make future S3/MinIO support harder to isolate.

2. Write files under a deterministic, safe local layout.

   Store local content at a path derived from task ID, artifact type, version, generated artifact ID, and a sanitized display file name:

   ```text
   {artifact_root}/
     {task_id}/
       {artifact_type}/
         v{version}/
           {artifact_id}__{safe_name}
   ```

   Persist the relative path in `Artifact.storage.path` and expose `local://artifacts/{relative_path}` in `Artifact.storage.uri`. All reads must resolve the relative path under `artifact_root` and reject paths that escape the root.

   Alternative considered: use the flatter layout from `docs/backend.md`, such as `task_001/plc_code_v1.st`. The flatter layout is easier to inspect manually but becomes collision-prone once retries, multiple worker jobs, and same-version artifacts exist.

3. Make file writes effectively immutable.

   The service should generate artifact IDs before writing, write to a temporary file in the target directory, fsync or flush as practical, then atomically rename to the final path. It must reject writes if the final path already exists. If metadata persistence fails after the file is written, the service should best-effort remove the newly created file before re-raising the persistence error.

   Alternative considered: rely only on the database primary key for immutability. That does not protect local files from accidental overwrite if path generation or caller-provided names collide.

4. Keep content as bytes internally and decode only at the API edge.

   `write_artifact_content` should accept bytes, text, or JSON-compatible content and normalize to bytes for hashing and storage. `read_artifact_content` should return bytes plus the validated `Artifact`. The API endpoint can return a JSON envelope with UTF-8 text content for text-like artifacts used by PLC code, reports, traces, patches, and final reports.

   Alternative considered: store and return only strings. That works for the current examples but would make future bundles or binary evidence awkward.

5. Synchronize task artifact pointers in the service.

   After creating an artifact record, the service should update `TaskState.current_artifacts.all_artifact_ids` and the matching current/latest pointer when the artifact type has a dedicated field:

   ```text
   raw_user_request     -> raw_user_request
   requirements_ir      -> requirements_ir
   plc_code             -> current_code
   io_contract          -> current_io_contract
   test_cases           -> latest_test_cases
   test_report          -> latest_test_report
   failing_trace        -> latest_failing_trace
   formal_properties    -> latest_formal_properties
   formal_report        -> latest_formal_report
   counterexample       -> latest_counterexample
   patch                -> latest_patch
   repair_summary       -> latest_repair_summary
   gate_report          -> latest_gate_report
   final_report         -> final_report
   ```

   Artifact types without a dedicated pointer, such as worker logs or miscellaneous artifacts, should still be added to `all_artifact_ids`.

   Alternative considered: leave task state synchronization to future worker result handling. That would make the store pass its own tests while leaving future `WorkerInput` construction without a reliable current artifact source.

6. Add read-only API endpoints now, keep write APIs out.

   Add `GET /api/tasks/{task_id}/artifacts` to list stored artifacts for a task and `GET /api/artifacts/{artifact_id}` to return artifact metadata plus UTF-8 content. These endpoints should be wired in `create_app`. They should use the existing synchronous SQLAlchemy session dependency pattern.

   Alternative considered: defer all API work. `docs/backend.md` includes curl checks for artifact list and content retrieval, so read-only endpoints are part of making this milestone verifiable.

## Risks / Trade-offs

- [Risk] Filesystem and database writes cannot be made fully atomic in one transaction. -> Mitigation: write to a unique path, persist metadata after the final rename, and best-effort delete the new file if metadata persistence fails.
- [Risk] Path traversal or malformed stored paths could expose files outside `ARTIFACT_ROOT`. -> Mitigation: never trust raw stored paths; resolve and verify every read stays under the configured root.
- [Risk] API text decoding may not work for future binary artifacts. -> Mitigation: keep the service byte-oriented and make the initial API behavior explicit for UTF-8/text-like artifacts.
- [Risk] Updating `TaskState.current_artifacts` during artifact creation could conflict with future worker result handling semantics. -> Mitigation: keep the mapping simple, type-based, and additive; worker-specific status/gate changes remain out of scope.
- [Risk] Existing database persistence change is still unarchived and the worktree is dirty. -> Mitigation: implement this change on top of the current repository state without reverting unrelated work.

## Migration Plan

No data migration is required. The change uses the existing `artifacts` table shape and the existing `ARTIFACT_ROOT` setting.

Deployment steps:

1. Deploy code with the new service and read-only endpoints.
2. Ensure the local process can create directories under `ARTIFACT_ROOT`.
3. Run the development script to create representative artifacts in a local environment.

Rollback is code-only unless local artifacts have already been created. Existing metadata rows and local files can remain inert if the service is rolled back.

## Open Questions

- Should future non-text artifacts return bytes directly from `GET /api/artifacts/{artifact_id}` or use a separate download endpoint?
- Should artifact version monotonicity per `(task_id, type)` be enforced later at the service layer or by an additional database constraint?
