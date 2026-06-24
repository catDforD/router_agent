# task-intake-classification Specification

## Purpose
Preserve conservative task creation while allowing the default Main Agent
tool loop to run without a standalone structured Intake classifier.

## Requirements

### Requirement: Task API creation remains conservative
The backend SHALL keep the public task creation contract conservative.

#### Scenario: New task starts with unknown intake state
- **WHEN** a client creates a task through `POST /api/tasks`
- **THEN** the initial persisted `TaskState` has `status: "created"`, `phase: "intake"`, `task_type: "unknown"`, and `difficulty.level: "L0"`

### Requirement: Runtime starts without standalone intake classification
The backend SHALL start Main Agent orchestration for a newly created task without first obtaining a separate structured intake classification model output.

#### Scenario: Created task can enter tool-loop orchestration
- **WHEN** Runtime starts execution for a task with `status` equal to `created`, `phase` equal to `intake`, `task_type` equal to `unknown`, and `difficulty.level` equal to `L0`
- **THEN** Runtime starts the Main Agent tool-loop orchestration directly
- **AND** no standalone intake model call or structured classification object is required before the first orchestration turn

### Requirement: Worker dispatch still requires prepared state
The backend SHALL prevent worker side effects until the task is prepared for worker dispatch or the selected domain tool can prepare it deterministically.

#### Scenario: Direct worker call for unprepared task is rejected
- **WHEN** a PLC worker call is proposed for a task whose `status` is `created`, `phase` is `intake`, or `task_type` is `unknown`
- **THEN** Scheduler Guard rejects the worker call
- **AND** no worker job, worker event, artifact, or task mutation is created by that rejected call

#### Scenario: Domain tool prepares created task before worker dispatch
- **WHEN** the default tool loop calls a configured PLC/domain MCP tool for a newly created intake task
- **THEN** the backend prepares the task context with runnable `status`, `phase`, `task_type`, and `normalized_goal`
- **AND** it emits an observable `task.updated` event before worker lifecycle events

### Requirement: Clarification uses explicit tools
The backend SHALL pause task execution for missing information through explicit tool/service calls rather than a structured intake classification output.

#### Scenario: Clarification request pauses task
- **WHEN** the Main Agent scripted runner or runtime service requests clarification with at least one required question
- **THEN** the persisted task has `status` equal to `waiting_user`, `phase` equal to `clarifying`, and open unresolved clarification questions
- **AND** no PLC worker job is created by that clarification path

#### Scenario: Clarification without questions is rejected
- **WHEN** clarification is requested without any question
- **THEN** Runtime rejects the request
- **AND** does not update task state from that invalid request
