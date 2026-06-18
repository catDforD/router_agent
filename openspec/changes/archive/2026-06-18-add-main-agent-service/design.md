## Context

The Router backend already has the deterministic runtime pieces needed for a mock worker flow:

- strict Router v1 Pydantic models
- persisted task, artifact, event, worker job, and gate result repositories
- local artifact store with `TaskState.current_artifacts` projection updates
- scheduler guard checks that reject illegal worker dispatch
- quality gate and finish tools
- mock MCP adapter and worker result handler
- SDK-facing function tools in `backend/app/agents/tools.py`

The missing boundary is the Main Agent service. `TaskService.create_task()` intentionally creates a conservative `created/intake/unknown/L0` task, while `AgentToolService` intentionally rejects worker calls for that unclassified state. Therefore step 15 needs both a Main Agent episode wrapper and a minimal intake classification application path before orchestration tools can run.

```text
POST /api/tasks
    |
    v
created / intake / unknown / L0
    |
    v
MainAgentService.run_episode(task_id)
    |
    +--> intake classification
    |       |
    |       +--> waiting_user / clarifying
    |       |
    |       +--> running / planning
    |
    +--> orchestration with existing function tools
            |
            +--> dev / test / formal / repair / gate / finish
```

`runtime_service.py` remains the later background loop integration point. This change should make an episode callable directly from tests and scripts without changing public HTTP task creation behavior.

## Goals / Non-Goals

**Goals:**

- Provide `MainAgentService` as the backend boundary for one task episode.
- Construct OpenAI Agents SDK agents with instructions, existing function tools, structured outputs, and runtime context.
- Build compact task state views that reference artifacts instead of embedding large content.
- Classify `created/intake/unknown` tasks before any PLC worker tool can run.
- Apply validated classification decisions to `TaskState` with deterministic safety gate elevation.
- Emit observable Main Agent and task transition events.
- Persist `openai_trace_id` and `main_agent_run_id` so worker inputs inherit trace linkage.
- Keep core behavior testable with a fake agent runner and mock MCP workers.

**Non-Goals:**

- Do not start background work from `POST /api/tasks`; that belongs to Runtime Loop.
- Do not add public HTTP endpoints.
- Do not change Router v1 schema, JSON Schema, TypeScript declarations, or database tables.
- Do not bypass `AgentToolService`, Scheduler Guard, Quality Gate, or WorkerResult Handler.
- Do not generate final report artifacts; final report synthesis remains a later step.
- Do not implement real MCP client behavior; existing mock adapter remains sufficient for this change.

## Decisions

### Split the episode into intake and orchestration phases

Use a dedicated intake classification phase for unclassified tasks. The intake phase returns an internal `IntakeClassificationOutput` and never receives PLC worker tools. After the decision is validated and applied, orchestration can use the existing function tools.

This matches the current guard boundary: worker tools require a classified running task. It also prevents the Main Agent from calling `call_plc_dev` before `TaskState` contains task type, difficulty, gate requirements, and normalized goal.

Alternative considered: let the orchestrator call tools immediately and react to guard rejection. Rejected because every newly created task would first produce a known rejected worker attempt, adding noisy traces and no useful state.

### Keep structured output models internal

Define internal Pydantic models in `output_schema.py`:

- `IntakeClassificationOutput`
- `MainAgentEpisodeOutput`
- `MainAgentDecision`
- `MainAgentPlanStep`
- `MainAgentArtifactReference`

These models validate Main Agent output but do not become Router v1 cross-service contracts. Runtime maps the classification output onto existing `TaskState` fields.

Alternative considered: add new Router v1 schemas. Rejected because no external consumer needs these intermediate agent outputs yet, and adding public contracts would require JSON Schema and TypeScript updates.

### Runtime applies and elevates classification

Classification application should live in the Main Agent service boundary or a small helper owned by it. The helper loads the latest task, validates the classification output, applies deterministic elevation rules from the existing intake classification spec, then persists:

- `normalized_goal`
- `task_type`
- `difficulty`
- `gates`
- `status`
- `phase`
- `unresolved_questions`, when clarification is required
- `updated_at`

Safety signals such as emergency stop, interlock, fault latching, mode switching, state machine, or general safety constraints must elevate the task to at least `L3`, require tests, and require formal verification.

Alternative considered: trust model-provided gate flags. Rejected because scheduler and quality policy must remain deterministic even when model output is optimistic.

### Use compact state views as agent input

Build a `state_view` dictionary from persisted `TaskState` containing only compact fields:

- task id, user goal, normalized goal, status, phase
- task type and difficulty summary
- gate flags and latest pass/fail markers
- current artifact refs and summaries
- open failure summaries
- repair round counters and worker call budget
- available tools for the phase

The view should not include full PLC code, full reports, logs, counterexamples, or artifact file content. If the agent needs content, it must call `read_artifact` in bounded mode.

Alternative considered: pass full task JSON and artifact contents. Rejected because the project rule keeps large content artifactized and Main Agent context compact.

### Keep SDK wrappers thin and injectable

`MainAgentService` should have a core method such as `run_episode(task_id)` and accept an injectable runner abstraction for tests. The production runner uses:

- `Agent(..., instructions=..., tools=get_main_agent_tools(), output_type=...)`
- `Runner.run(..., context=AgentToolContext(...), max_turns=..., run_config=...)`
- `RunConfig(workflow_name=..., trace_id=..., group_id=task_id, trace_metadata=...)`

Unit and integration tests can provide fake structured outputs without calling OpenAI models. This keeps local tests deterministic and avoids requiring `OPENAI_API_KEY`.

Alternative considered: put `Runner.run` directly in tests. Rejected because model behavior is nondeterministic and external API availability should not gate unit coverage.

### Persist trace and Main Agent events early

Before a run starts, generate or reuse an `openai_trace_id` and generate a `main_agent_run_id`, update `TaskState.trace`, and emit `main_agent.started`. Main Agent decisions should be emitted as compact `main_agent.decision` or `main_agent.plan_updated` events. When finalizing, emit `main_agent.finalizing` before calling `run_quality_gate` / `finish_task`.

This is not the full Trace Mapping milestone, but it establishes the IDs that worker inputs already know how to inherit.

Alternative considered: postpone trace fields until the later Trace Mapping change. Rejected because worker input builder already reads `TaskState.trace`; leaving it empty makes later event correlation harder to backfill.

### Keep orchestration policy prompt-driven but runtime-enforced

Instructions should tell the Main Agent to follow the documented flow:

- QA can answer without workers, then quality gate and finish.
- New PLC development usually calls `call_plc_dev`.
- L2+ calls `call_plc_test`.
- L3/L4 or safety-critical signals call `call_plc_formal`.
- Failures call `call_plc_repair`.
- Repair requires regression test and, after formal failure, formal regression.
- Final success requires `run_quality_gate` and `finish_task`.

The service should not duplicate all planning logic as deterministic code. Scheduler Guard, Quality Gate, and tool results remain the enforcement layer.

Alternative considered: implement a fixed runtime planner for all mock scenarios. Rejected because step 15 is specifically about integrating Main Agent behavior; fixed planning belongs only in tests or as a fake runner.

## Risks / Trade-offs

- [Risk] Real model orchestration may skip a required worker. -> Mitigation: Scheduler Guard and Quality Gate reject unsafe finish; integration tests use fake runner paths for required scenarios.
- [Risk] Classification output can be too weak for safety-critical requests. -> Mitigation: deterministic elevation rules update difficulty and gate flags before worker dispatch.
- [Risk] Fake runner tests can pass while real prompts fail. -> Mitigation: keep fake tests for contracts and add a small optional live-agent smoke test that is skipped without `OPENAI_API_KEY`.
- [Risk] Main Agent context can grow through artifact reads. -> Mitigation: default state view uses summaries only; `read_artifact(full)` remains bounded and marks truncation.
- [Risk] Episode code and future Runtime Loop code may overlap. -> Mitigation: make `MainAgentService.run_episode(task_id)` a reusable synchronous/async unit that Runtime Loop can call later.
- [Risk] Agent SDK API changes within the broad dependency range could break wrappers. -> Mitigation: isolate SDK imports in `main_agent.py` and keep tests focused on the service boundary plus current installed SDK shape.

## Migration Plan

No database or Router v1 schema migration is required.

Implementation can be rolled out behind tests:

1. Add internal output schemas and instructions.
2. Add classification validation/application helpers.
3. Add `MainAgentService` with injectable runner and production SDK runner.
4. Add unit tests for state view, classification application, event emission, and trace persistence.
5. Add integration tests that drive mock happy paths through fake agent episodes and existing `AgentToolService`.

Rollback removes the new agent service files and tests. Existing API, repository, artifact, event, worker, and quality gate behavior remains intact.

## Open Questions

- Should `MAIN_AGENT_MODEL` and `MAIN_AGENT_MAX_TURNS` be added to `Settings` in this change, or should defaults remain local to `MainAgentService` until Runtime Loop integration?
- Should live OpenAI integration tests exist as skipped tests, or should live validation remain a local script only?
- Should classification output be stored as a `main_agent_log` artifact now, or only as compact event payloads until final trace/report work?
