## Why

Router artifact metadata can now be persisted, but artifact content still has no durable backend boundary. The next runtime step needs a local Artifact Store so large code, reports, traces, patches, and final deliverables can be written once, read by API and worker flows, and referenced through stable Router v1 `ArtifactRef` objects.

## What Changes

- Add a local artifact content store rooted at the existing `ARTIFACT_ROOT` setting.
- Add an Artifact Store service that writes immutable content files, computes `sha256` hashes and byte sizes, creates Router v1 `Artifact` metadata records, reads stored content by artifact ID, builds `ArtifactRef` values, and lists artifacts by task.
- Extend artifact metadata repository access with task-scoped listing and ref-oriented helpers needed by the service.
- Keep large artifact content externalized through `storage.uri` and `storage.path`; do not place large content in `inline_content`.
- Add read-only FastAPI artifact endpoints:
  - `GET /api/tasks/{task_id}/artifacts`
  - `GET /api/artifacts/{artifact_id}`
- Add focused tests and a development script for creating representative local artifacts and inspecting them through service/API behavior.

## Capabilities

### New Capabilities

- `local-artifact-store`: Stores Router artifact content in the local filesystem, persists metadata, exposes artifact references, and provides read-only artifact listing/content retrieval APIs.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `backend/app/services/artifact_store.py` for the local store service.
  - `backend/app/repositories/artifact_repo.py` for task-scoped listing and reference retrieval helpers.
  - `backend/app/api/artifacts.py` and `backend/app/main.py` for read-only artifact endpoints.
  - `backend/app/core/errors.py` for store-level not found, conflict, or invalid storage errors if repository errors are not sufficient.
  - `backend/app/tests/unit/test_artifact_store.py` and API tests for content, hash, immutability, metadata persistence, and endpoint behavior.
  - `scripts/dev_create_artifacts.py` for manual local artifact creation.
- Uses existing dependencies and the existing Router v1 `Artifact`, `ArtifactStorage`, and `ArtifactRef` contract shapes.
- No Router v1 contract strings, enum values, JSON Schema files, or TypeScript declarations are changed.
- No S3, MinIO, upload endpoint, worker scheduling, event streaming, or Main Agent tool integration is implemented in this change.
