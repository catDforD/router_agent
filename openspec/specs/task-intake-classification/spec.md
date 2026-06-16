# task-intake-classification Specification

## Purpose
Classify conservatively created Router tasks before worker execution so Runtime can apply validated task type, difficulty, gate, and clarification state to `TaskState`.

## Requirements

### Requirement: Runtime classifies intake tasks before worker execution
The backend SHALL classify a created intake task before Runtime dispatches any PLC worker that requires task type or difficulty context.

#### Scenario: Created task is classified before first worker call
- **WHEN** Runtime starts execution for a task with `status` equal to `created`, `phase` equal to `intake`, `task_type` equal to `unknown`, and `difficulty.level` equal to `L0`
- **THEN** Runtime obtains an intake classification decision before creating any worker job

#### Scenario: Task API creation remains conservative
- **WHEN** a client creates a task through `POST /api/tasks`
- **THEN** the initial persisted `TaskState` remains `status: "created"`, `phase: "intake"`, `task_type: "unknown"`, and `difficulty.level: "L0"` until Runtime intake classification runs

### Requirement: Intake classification output is structured
The backend SHALL represent Main Agent intake classification as a structured object that can be validated before it is applied to `TaskState`.

#### Scenario: Valid classification includes required state fields
- **WHEN** the Main Agent returns an intake classification decision
- **THEN** the decision contains a normalized goal, task type, difficulty level, difficulty reasons, difficulty signals, test requirement, formal requirement, repair-loop requirement, and clarification flag

#### Scenario: Invalid classification is rejected
- **WHEN** the Main Agent returns an intake classification decision with an invalid task type, invalid difficulty level, or missing required difficulty signals
- **THEN** Runtime rejects the decision and does not update the task state from that decision

### Requirement: Runtime applies validated classification to TaskState
The backend SHALL persist validated intake classification results to the current task state.

#### Scenario: Classified task moves to planning
- **WHEN** Runtime applies a valid classification decision that does not require clarification
- **THEN** the persisted `TaskState` contains the normalized goal, task type, difficulty profile, and gate requirements from the validated decision
- **AND** the task has `status` equal to `running` and `phase` equal to `planning`

#### Scenario: Classification updates task timestamp
- **WHEN** Runtime applies a valid classification decision
- **THEN** the persisted `TaskState.updated_at` value is advanced

### Requirement: Runtime elevates safety-critical gates
The backend MUST enforce deterministic minimum difficulty and gate requirements for safety-critical PLC tasks regardless of the Main Agent's requested values.

#### Scenario: L2 or higher task requires tests
- **WHEN** a classification decision has `difficulty.level` equal to `L2`, `L3`, or `L4`
- **THEN** the persisted task has `gates.test_required` equal to true and `difficulty.requires_test` equal to true

#### Scenario: Emergency stop requires formal verification
- **WHEN** a classification decision has `difficulty.signals.has_emergency_stop` equal to true
- **THEN** the persisted task has difficulty at least `L3`, `gates.test_required` equal to true, and `gates.formal_required` equal to true

#### Scenario: Interlock requires formal verification
- **WHEN** a classification decision has `difficulty.signals.has_interlock` equal to true
- **THEN** the persisted task has difficulty at least `L3`, `gates.test_required` equal to true, and `gates.formal_required` equal to true

#### Scenario: Fault latching requires formal verification
- **WHEN** a classification decision has `difficulty.signals.has_fault_latching` equal to true
- **THEN** the persisted task has difficulty at least `L3`, `gates.test_required` equal to true, and `gates.formal_required` equal to true

#### Scenario: Mode switching or state machine requires formal verification
- **WHEN** a classification decision has `difficulty.signals.has_mode_switching` equal to true or `difficulty.signals.has_state_machine` equal to true
- **THEN** the persisted task has difficulty at least `L3`, `gates.test_required` equal to true, and `gates.formal_required` equal to true

### Requirement: Clarification-required classification pauses worker execution
The backend SHALL pause task execution and ask for clarification when intake classification determines that the requirement is incomplete.

#### Scenario: Classification asks user for missing information
- **WHEN** Runtime applies a classification decision with `need_clarification` equal to true and at least one clarification question
- **THEN** the persisted task has `status` equal to `waiting_user`, `phase` equal to `clarifying`, and open unresolved clarification questions
- **AND** Runtime does not create a PLC worker job for that task

#### Scenario: Clarification decision without questions is rejected
- **WHEN** Runtime receives a classification decision with `need_clarification` equal to true and no clarification questions
- **THEN** Runtime rejects the decision and does not update the task state from that decision

### Requirement: Intake classification is observable
The backend SHALL record observable events when Runtime runs and applies intake classification.

#### Scenario: Classification emits Main Agent decision event
- **WHEN** Runtime receives an intake classification decision from the Main Agent
- **THEN** the task event log contains a `main_agent.decision` event summarizing the classification decision

#### Scenario: Classification state change emits task update event
- **WHEN** Runtime applies a classification decision to `TaskState`
- **THEN** the task event log contains a `task.updated` event correlated with the classification update

#### Scenario: Clarification emits waiting-user event
- **WHEN** Runtime applies a classification decision that moves the task to `waiting_user`
- **THEN** the task event log contains a `task.waiting_user` event identifying the open clarification questions
