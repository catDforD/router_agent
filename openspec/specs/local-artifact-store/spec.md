# local-artifact-store Specification

## Purpose
Stores Router artifact content in the local filesystem, persists metadata, exposes artifact references, and provides read-only artifact listing and content retrieval APIs.
## Requirements
### Requirement: Local artifact content is written and read durably
The backend SHALL provide a local Artifact Store service that writes artifact content under the configured artifact root and reads the same content by artifact ID.

#### Scenario: Write then read artifact content
- **WHEN** artifact content is written for a task, artifact type, version, name, summary, visibility, creator, parent artifact IDs, and metadata
- **THEN** reading the returned artifact ID returns the same content bytes and a validated Router v1 `Artifact`

#### Scenario: Content hash and size reflect stored bytes
- **WHEN** artifact content is written through the local Artifact Store service
- **THEN** the stored `Artifact.storage.content_hash` is `sha256:<hex>` for the exact stored bytes and `Artifact.storage.size_bytes` equals the stored byte length

#### Scenario: Large content is externalized
- **WHEN** artifact content is written through the local Artifact Store service
- **THEN** the persisted `Artifact.inline_content` is `null` and the content is addressed through `Artifact.storage.uri` and `Artifact.storage.path`

### Requirement: Artifact metadata is persisted with local storage details
The backend SHALL persist a complete Router v1 `Artifact` metadata record for each locally stored artifact.

#### Scenario: Metadata record is created after content write
- **WHEN** artifact content is written through the local Artifact Store service
- **THEN** the artifact metadata can be read through the artifact repository with provider `local`, a `local://artifacts/` URI, a relative local storage path, MIME type when supplied or inferred, content hash, summary, visibility, parent references, creator, timestamps, and metadata

#### Scenario: Artifact reference is available for worker inputs
- **WHEN** an artifact has been stored
- **THEN** `get_artifact_ref` returns an `ArtifactRef` containing the artifact ID, type, version, URI, summary, and content hash from the stored metadata

### Requirement: Artifact writes are immutable
The backend SHALL avoid overwriting existing artifact content or metadata when storing artifacts.

#### Scenario: Duplicate artifact ID is rejected
- **WHEN** local artifact storage attempts to create metadata for an artifact ID that already exists
- **THEN** the write is rejected instead of updating the existing artifact row or replacing existing content

#### Scenario: Same artifact type new version does not overwrite previous content
- **WHEN** two artifacts are written for the same task and artifact type with different versions
- **THEN** both artifact records remain readable and each artifact reads its own stored content

#### Scenario: Existing local path is not overwritten
- **WHEN** a local artifact write resolves to a final path that already exists
- **THEN** the service rejects the write instead of replacing the existing file

### Requirement: Task artifact listing is available
The backend SHALL list artifacts associated with a task from persisted metadata.

#### Scenario: List artifacts for a task
- **WHEN** multiple artifacts have been stored for a task
- **THEN** `list_task_artifacts` returns the task artifacts ordered by creation time and version using validated Router v1 `Artifact` metadata

#### Scenario: Task state artifact references are updated
- **WHEN** an artifact is stored for a task with an existing `TaskState`
- **THEN** the task state's `current_artifacts.all_artifact_ids` includes the new artifact ID and the matching current or latest artifact pointer is updated when the artifact type has a dedicated pointer

### Requirement: Artifact API exposes stored artifacts read-only
The backend SHALL expose read-only HTTP endpoints for artifact listing and artifact content retrieval.

#### Scenario: Task artifact list endpoint returns metadata
- **WHEN** a client calls `GET /api/tasks/{task_id}/artifacts` for a task with stored artifacts
- **THEN** the response contains the persisted artifact metadata for that task without reading or embedding large file content

#### Scenario: Artifact content endpoint returns metadata and text content
- **WHEN** a client calls `GET /api/artifacts/{artifact_id}` for a stored UTF-8 text artifact
- **THEN** the response contains the artifact metadata, UTF-8 content text, content encoding, MIME type, size, and content hash

#### Scenario: Missing artifact returns not found
- **WHEN** a client calls an artifact API endpoint for an unknown artifact ID or task ID
- **THEN** the response indicates the requested artifact or task artifact list was not found using an HTTP not found status

### Requirement: Local artifact paths are constrained to the artifact root
The backend SHALL constrain all local artifact reads and writes to paths under the configured artifact root.

#### Scenario: Stored path escaping artifact root is rejected
- **WHEN** artifact metadata contains a local storage path that resolves outside the configured artifact root
- **THEN** reading artifact content is rejected with an invalid storage error

#### Scenario: Artifact names are sanitized for filesystem paths
- **WHEN** artifact content is written with a name containing path separators or unsafe filename characters
- **THEN** the service stores the file under a sanitized filename inside the task, artifact type, and version directory

### Requirement: Artifact store persists Main Agent final reports
The backend SHALL persist validated Main Agent episode reports as local artifacts using the existing `FINAL_REPORT` artifact type.

#### Scenario: Final report artifact is written
- **WHEN** a Main Agent orchestration episode produces a valid final output
- **THEN** the Artifact Store writes a `FINAL_REPORT` artifact for the task with user visibility, Router v1 metadata, local storage details, content hash, size, and creator type `main_agent`

#### Scenario: Final report artifact is readable
- **WHEN** a client reads the final report artifact through the artifact API
- **THEN** the response includes artifact metadata and UTF-8 report content without requiring access to runtime memory

### Requirement: Artifact store persists Main Agent replay logs
The backend SHALL persist normalized Main Agent replay logs as local artifacts using the existing `MAIN_AGENT_LOG` artifact type.

#### Scenario: Replay log artifact is written
- **WHEN** a Main Agent orchestration episode records turn observability entries
- **THEN** the Artifact Store writes a `MAIN_AGENT_LOG` artifact for the task with local storage details, content hash, size, and creator type `main_agent`

#### Scenario: Replay log can be internal
- **WHEN** a replay log contains SDK diagnostics, raw response metadata, or other implementation details not intended for the user timeline
- **THEN** the artifact visibility can be `internal` while the user-visible `main_agent.completed` event still references the user-visible `FINAL_REPORT`

### Requirement: Final report and replay log are linked to task artifact state
The backend SHALL add final report and replay log artifact IDs to the task's artifact collection.

#### Scenario: Task artifact list includes report artifacts
- **WHEN** final report and replay log artifacts are written for a task
- **THEN** `current_artifacts.all_artifact_ids` includes both artifact IDs
- **AND** `GET /api/tasks/{task_id}/artifacts` returns the user-visible final report artifact in creation order
