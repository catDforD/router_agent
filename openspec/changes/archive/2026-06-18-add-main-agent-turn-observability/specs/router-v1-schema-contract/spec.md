## ADDED Requirements

### Requirement: Router v1 supports Main Agent observability event types
The Router v1 schema contract SHALL include event types needed to represent Main Agent orchestration turn progress, tool calls, tool results, and completion.

#### Scenario: Main Agent turn event type validates
- **WHEN** a Router event payload uses type `main_agent.turn_started`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the event

#### Scenario: Main Agent tool event types validate
- **WHEN** Router event payloads use types `main_agent.tool_called` and `main_agent.tool_result`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the events

#### Scenario: Main Agent completed event type validates
- **WHEN** a Router event payload uses type `main_agent.completed`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the event

### Requirement: Main Agent completed events reference report artifacts
The Router v1 schema contract SHALL allow `main_agent.completed` events to reference final report and replay log artifacts through existing event correlation and payload fields.

#### Scenario: Completed event carries artifact references
- **WHEN** a `main_agent.completed` event is created after a successful episode
- **THEN** the event correlation includes the final report artifact ID and replay log artifact ID when available
- **AND** the payload includes `final_report_artifact_id`, `main_agent_log_artifact_id`, `final_task_status`, and compact summary fields

### Requirement: Final report and Main Agent log artifact types remain stable
The Router v1 schema contract SHALL use existing artifact type values `final_report` and `main_agent_log` for Main Agent report and replay artifacts.

#### Scenario: Final report artifact validates
- **WHEN** a Router artifact payload uses type `final_report`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the artifact

#### Scenario: Main Agent log artifact validates
- **WHEN** a Router artifact payload uses type `main_agent_log`
- **THEN** backend Pydantic validation and exported JSON Schema validation accept the artifact

### Requirement: TypeScript declarations include Main Agent observability values
The TypeScript Router contract declaration SHALL include the Main Agent observability event type values and existing report artifact type values.

#### Scenario: TypeScript event union includes observability events
- **WHEN** a TypeScript consumer imports the Router event type declarations
- **THEN** the event type union includes `main_agent.turn_started`, `main_agent.tool_called`, `main_agent.tool_result`, and `main_agent.completed`
