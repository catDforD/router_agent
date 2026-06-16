## MODIFIED Requirements

### Requirement: Scheduler Guard validates successful task completion
The backend SHALL reject attempts to finish a task as `succeeded` while required evidence, regression work, clarification, blocking failure state, or a passing Quality Gate marker is absent.

#### Scenario: Finish succeeded without passing Quality Gate is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded` and `TaskState.gates.can_finish_as_success` is not true
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: Finish succeeded with blocking failure is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded` and the task has `gates.has_blocking_failure` equal to true or an open blocking failure
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: Finish succeeded without required test is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded`, `TaskState.gates.test_required` is true, and `TaskState.gates.latest_test_passed` is not true
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: L3 task skipping formal is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded`, `TaskState.gates.formal_required` is true, and `TaskState.gates.latest_formal_passed` is not true
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: Finish succeeded during required regression is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded` and `TaskState.gates.regression_required` is true
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: Finish succeeded during required formal regression is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded` and `TaskState.gates.formal_regression_required` is true
- **THEN** Scheduler Guard rejects the finish action

#### Scenario: Finish succeeded with required clarification is rejected
- **WHEN** Runtime proposes `final_status` equal to `succeeded` and the task has an open required clarification question
- **THEN** Scheduler Guard rejects the finish action
