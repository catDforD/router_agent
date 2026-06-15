## 1. Repository and Service Foundations

- [x] 1.1 Add store-level error types or reuse repository errors for missing artifacts, write conflicts, invalid local storage paths, unsupported providers, and unreadable content.
- [x] 1.2 Extend `ArtifactRepository` with `list_task_artifacts(task_id)` ordered by creation time/version and `get_artifact_ref(artifact_id)`.
- [x] 1.3 Define local Artifact Store request/result shapes in `backend/app/services/artifact_store.py` for content writes and content reads without changing Router v1 schemas.
- [x] 1.4 Add helper functions for content normalization, safe filename generation, local relative path construction, `local://artifacts/` URI construction, and artifact-root path validation.

## 2. Local Artifact Store Implementation

- [x] 2.1 Implement `write_artifact_content` to normalize content to bytes, compute `sha256:<hex>` and byte size, write through a temporary file, and atomically rename to the final immutable path.
- [x] 2.2 Construct and persist a complete Router v1 `Artifact` metadata payload with provider `local`, URI, relative path, MIME type, hash, size, summary, parent references, creator, timestamps, metadata, and `inline_content=None`.
- [x] 2.3 Ensure duplicate artifact IDs and existing final local paths are rejected without overwriting previous metadata or content.
- [x] 2.4 Implement cleanup of newly written files when metadata persistence fails after the content write.
- [x] 2.5 Implement `read_artifact_content(artifact_id)` to load metadata, validate local provider/path safety, read stored bytes, and return bytes plus the validated `Artifact`.
- [x] 2.6 Implement service helpers `create_artifact_record`, `get_artifact_ref`, and `list_task_artifacts` using the repository methods.
- [x] 2.7 Update the associated task state's `current_artifacts.all_artifact_ids` and matching current/latest artifact pointer after successful artifact creation.

## 3. Read-Only Artifact API

- [x] 3.1 Implement `backend/app/api/artifacts.py` with database session dependencies and Artifact Store construction from app settings.
- [x] 3.2 Add `GET /api/tasks/{task_id}/artifacts` to return persisted artifact metadata for the task without embedding content.
- [x] 3.3 Add `GET /api/artifacts/{artifact_id}` to return artifact metadata plus UTF-8 text content, encoding, MIME type, size, and content hash for text artifacts.
- [x] 3.4 Translate missing artifact/task records and invalid local storage paths into appropriate HTTP error responses.
- [x] 3.5 Include the artifact API router from `create_app`.

## 4. Development Script

- [x] 4.1 Add `scripts/dev_create_artifacts.py` to create representative local artifacts through the service for an existing or seeded task.
- [x] 4.2 Have the script print artifact IDs, local URIs, hashes, and example curl commands for the list and content endpoints.

## 5. Tests

- [x] 5.1 Add unit tests that writing content then reading by artifact ID returns the same bytes and a validated Router v1 `Artifact`.
- [x] 5.2 Test that `content_hash`, `size_bytes`, `storage.uri`, `storage.path`, and `inline_content=None` match the stored content.
- [x] 5.3 Test that writing different versions for the same task/artifact type does not overwrite previous content.
- [x] 5.4 Test duplicate artifact ID rejection and existing final path rejection.
- [x] 5.5 Test local path traversal protection when stored metadata points outside `ARTIFACT_ROOT`.
- [x] 5.6 Test repository/service task artifact listing and `get_artifact_ref`.
- [x] 5.7 Test task state `current_artifacts` updates for a mapped artifact type and `all_artifact_ids` updates for all artifact types.
- [x] 5.8 Add API tests for task artifact listing, artifact content retrieval, missing artifact responses, and invalid storage responses.

## 6. Validation

- [x] 6.1 Run `uv run pytest backend/app/tests/unit/test_artifact_store.py -q`.
- [x] 6.2 Run the artifact API tests.
- [x] 6.3 Run `uv run python -m compileall backend`.
- [x] 6.4 Run `git diff --check`.
