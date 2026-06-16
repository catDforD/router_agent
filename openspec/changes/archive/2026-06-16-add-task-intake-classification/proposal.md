## Why

Task creation currently produces a durable `TaskState` shell with conservative `unknown/L0` classification, which is correct for the Task API milestone but not enough for Runtime to safely choose workers. Before the first worker call, the backend needs a structured intake classification step that lets the Main Agent assess task type and difficulty while Runtime enforces safety-critical gates.

## What Changes

- Add a Main Agent intake classification capability that updates an existing task from conservative bootstrap values to a classified `TaskState`.
- Define a structured classification result containing normalized goal, task type, difficulty profile, requirement signals, gate requirements, and clarification need.
- Add Runtime validation for classification decisions so safety-related signals such as emergency stop, interlock, fault latching, mode switching, and state machines cannot bypass test/formal gates.
- Emit observable classification events using existing Router event types, without adding new Router v1 enum values.
- Keep `POST /api/tasks` behavior unchanged: it still creates `created/intake/unknown/L0` tasks and does not synchronously call the Main Agent.

## Capabilities

### New Capabilities
- `task-intake-classification`: Classifies created Router tasks before worker execution and applies validated task type, difficulty, gate, and clarification state to `TaskState`.

### Modified Capabilities
- None.

## Impact

- Affected backend modules:
  - `backend/app/agents/output_schema.py`
  - `backend/app/agents/instructions.py`
  - `backend/app/agents/main_agent.py`
  - `backend/app/services/runtime_service.py`
  - `backend/app/services/task_service.py`
  - `backend/app/services/scheduler_guard.py`
- Affected tests:
  - New unit tests for applying validated intake classification results to `TaskState`.
  - New unit tests for Runtime validation and safety gate elevation.
  - New integration tests with a mock Main Agent classification result.
- No change is expected to Router v1 schema files, TypeScript declarations, database migrations, Task API request/response shapes, or worker contracts.
