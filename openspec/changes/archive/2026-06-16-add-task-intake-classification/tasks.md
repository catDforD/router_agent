## 1. Classification Output Contract

- [ ] 1.1 Add an internal Main Agent intake classification output model with normalized goal, task type, difficulty profile, requirement signals, gate requirements, clarification flag, and optional clarification questions.
- [ ] 1.2 Add validation tests that reject missing required classification fields, invalid task types, invalid difficulty levels, and clarification-required output without questions.
- [ ] 1.3 Add fixture helpers for representative classification outputs: QA, normal PLC development, safety-critical PLC development, repair existing code, and need clarification.

## 2. Runtime Validation Policy

- [ ] 2.1 Implement deterministic validation/elevation logic for classification decisions before they are applied to `TaskState`.
- [ ] 2.2 Enforce that `L2`, `L3`, and `L4` classifications require tests.
- [ ] 2.3 Enforce that safety-critical signals such as emergency stop, interlock, fault latching, mode switching, state machine, or safety constraints elevate difficulty to at least `L3` and require formal verification.
- [ ] 2.4 Enforce that `repair_existing_code` classifications require repair-loop capability.
- [ ] 2.5 Preserve explicit Runtime-added reasons when classification difficulty or gates are elevated.

## 3. TaskState Application

- [ ] 3.1 Add service behavior that atomically applies a validated classification result to the current `TaskState`.
- [ ] 3.2 Update `normalized_goal`, `task_type`, `difficulty`, `gates`, `status`, `phase`, `updated_at`, and unresolved clarification questions according to the validated decision.
- [ ] 3.3 Move non-clarification classified tasks to `status="running"` and `phase="planning"`.
- [ ] 3.4 Move clarification-required tasks to `status="waiting_user"` and `phase="clarifying"` without creating worker jobs.
- [ ] 3.5 Keep `POST /api/tasks` creation behavior unchanged as `created/intake/unknown/L0`.

## 4. Runtime And Agent Integration

- [ ] 4.1 Add a Runtime intake entrypoint that obtains a classification decision before any PLC worker job is created.
- [ ] 4.2 Add a mock Main Agent classification path for deterministic integration tests without calling external model APIs.
- [ ] 4.3 Emit `main_agent.started`, `main_agent.decision`, `task.updated`, and when needed `task.waiting_user` events for the intake classification flow.
- [ ] 4.4 Ensure worker input building never dispatches PLC workers from an unclassified `unknown/L0` task unless the task is an explicitly handled no-worker QA flow.

## 5. Tests And Verification

- [ ] 5.1 Add unit tests for applying normal development classification to `TaskState`.
- [ ] 5.2 Add unit tests for safety-critical classification elevation to `L3`, test required, and formal required.
- [ ] 5.3 Add unit tests for repair classification setting repair-loop requirements.
- [ ] 5.4 Add unit tests for clarification-required classification pausing execution and creating open clarification questions.
- [ ] 5.5 Add integration tests showing a created task remains conservative until Runtime intake classification runs.
- [ ] 5.6 Add integration tests showing Runtime emits classification-related events and does not create worker jobs before classification.
- [ ] 5.7 Run focused pytest targets for classification, task service, Runtime intake, and existing Task API behavior.
