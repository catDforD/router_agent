# scheduler-guard Specification

## Purpose
TBD - created by archiving change add-scheduler-guard. Update Purpose after archive.
## Requirements
### Requirement: Scheduler Guard validates worker dispatch before side effects
The backend SHALL validate proposed PLC worker calls before creating worker jobs, writing worker events, or invoking MCP tools.

#### Scenario: Unprepared intake task dispatch is rejected
- **WHEN** Runtime proposes any PLC worker call for a task whose `status` is `created`, `phase` is `intake`, or `task_type` is `unknown`
- **THEN** Scheduler Guard rejects the worker call

#### Scenario: Waiting-user task dispatch is rejected
- **WHEN** Runtime proposes any PLC worker call for a task whose `status` is `waiting_user` or that has an open required clarification question
- **THEN** Scheduler Guard rejects the worker call

#### Scenario: Test before code is rejected
- **WHEN** Runtime proposes a `plc-test` worker call and `TaskState.current_artifacts.current_code` is absent
- **THEN** Scheduler Guard rejects the worker call

#### Scenario: Formal before code is rejected
- **WHEN** Runtime proposes a `plc-formal` worker call and `TaskState.current_artifacts.current_code` is absent
- **THEN** Scheduler Guard rejects the worker call

#### Scenario: Worker call limit is enforced
- **WHEN** Runtime proposes a worker call and `TaskState.runtime_limits.worker_calls_used` is greater than or equal to `TaskState.runtime_limits.max_worker_calls`
- **THEN** Scheduler Guard rejects the worker call

### Requirement: Scheduler Guard validates repair eligibility
The backend SHALL validate repair-specific preconditions before dispatching a `plc-repair` worker.

#### Scenario: Repair before code is rejected
- **WHEN** Runtime proposes a `plc-repair` worker call and `TaskState.current_artifacts.current_code` is absent
- **THEN** Scheduler Guard rejects the repair call

#### Scenario: Repair before blocking failure is rejected
- **WHEN** Runtime proposes a `plc-repair` worker call and the task has no open failure with `severity` equal to `blocking`
- **THEN** Scheduler Guard rejects the repair call

#### Scenario: Repair without evidence is rejected
- **WHEN** Runtime proposes a `plc-repair` worker call without test or formal failure evidence artifacts
- **THEN** Scheduler Guard rejects the repair call

#### Scenario: Fourth repair round is rejected
- **WHEN** Runtime proposes a `plc-repair` worker call and `TaskState.runtime_limits.repair_rounds` is greater than or equal to `TaskState.runtime_limits.max_repair_rounds`
- **THEN** Scheduler Guard rejects the repair call

### Requirement: Scheduler Guard validates parallel worker batches
The backend SHALL validate proposed parallel worker batches against concurrency, call-budget, and per-worker scheduling rules before creating any job from the batch.

#### Scenario: Parallel batch exceeding concurrency is rejected
- **WHEN** Runtime proposes a parallel worker batch where active workers plus proposed jobs exceeds `TaskState.runtime_limits.max_parallel_workers`
- **THEN** Scheduler Guard rejects the entire batch

#### Scenario: Parallel batch exceeding worker-call budget is rejected
- **WHEN** Runtime proposes a parallel worker batch where used worker calls plus proposed jobs exceeds `TaskState.runtime_limits.max_worker_calls`
- **THEN** Scheduler Guard rejects the entire batch

#### Scenario: Parallel batch with invalid member is rejected
- **WHEN** Runtime proposes a parallel worker batch and any proposed worker call violates Scheduler Guard worker dispatch rules
- **THEN** Scheduler Guard rejects the entire batch

#### Scenario: Parallel repair is rejected in v1
- **WHEN** Runtime proposes a parallel worker batch containing a `plc-repair` job
- **THEN** Scheduler Guard rejects the entire batch

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

### Requirement: Scheduler Guard reports deterministic violations without mutation
The backend SHALL make Scheduler Guard decisions deterministic and free of runtime side effects.

#### Scenario: Guard violation includes structured details
- **WHEN** Scheduler Guard rejects a proposed action
- **THEN** the rejection includes a stable violation code, a human-readable message, and optional details about the rejected action

#### Scenario: Rejected action does not mutate task state
- **WHEN** Scheduler Guard rejects a proposed action
- **THEN** the input `TaskState` remains unchanged
