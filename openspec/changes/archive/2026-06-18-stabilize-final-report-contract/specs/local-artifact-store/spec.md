## MODIFIED Requirements

### Requirement: Artifact store persists Main Agent final reports
The backend SHALL persist validated Main Agent delivery reports as local artifacts using the existing `FINAL_REPORT` artifact type and a stable compact Router v1 report payload.

#### Scenario: Final report artifact is written
- **WHEN** a Main Agent/Runtime finalization path produces a valid terminal delivery outcome of `succeeded`, `partial_failed`, or `failed`
- **THEN** the Artifact Store writes a `FINAL_REPORT` artifact for the task with user visibility, Router v1 metadata, local storage details, content hash, size, and creator type `main_agent`
- **AND** the artifact content includes `kind: "main_agent_final_report"`, `schema_version: "router.v1"`, and `report_version: 1`

#### Scenario: Final report payload is artifact-oriented
- **WHEN** the Artifact Store persists a `FINAL_REPORT` artifact
- **THEN** the report payload references PLC code, I/O contract, test report, formal report, counterexample, patch, repair summary, gate report, and replay log evidence by artifact ID when those artifacts exist
- **AND** the report payload does not embed full PLC code, full test logs, full formal reports, full counterexamples, full patches, raw worker logs, raw model output, raw MCP payloads, or hidden reasoning

#### Scenario: Final report artifact is readable
- **WHEN** a client reads the final report artifact through the artifact API
- **THEN** the response includes artifact metadata and UTF-8 JSON report content without requiring access to runtime memory
- **AND** the report content contains enough structured fields for a frontend to render the user's goal, final status, delivered artifacts, validation evidence, repairs, assumptions, and unresolved items
