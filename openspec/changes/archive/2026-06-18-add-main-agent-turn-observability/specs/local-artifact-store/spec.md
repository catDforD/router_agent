## ADDED Requirements

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
