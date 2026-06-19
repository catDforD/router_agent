import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.main_agent import (
    MAIN_AGENT_TOOL_NAMES,
    MainAgentService,
    MaxTurnsExceeded,
    ModelBehaviorError,
    OpenAIAgentsRunner,
    build_intake_agent,
    build_orchestration_agent,
    build_run_config,
    build_state_view,
    episode_output_from_task,
)
import app.agents.main_agent as main_agent_module
from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.output_schema import (
    IntakeClassificationOutput,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
)
from app.agents.tools import AgentToolContext
from app.core.errors import RepositoryNotFoundError
from app.models.db_models import ArtifactRow, Base, EventRow, TaskRow, WorkerJobRow
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactType,
    Failure,
    FailureReproduction,
    GateState,
    RuntimeLimits,
    Severity,
    TaskPhase,
    TaskStatus,
    TaskState,
)
from app.repositories.task_repo import TaskRepository
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
from app.services.event_service import EventService
from app.services.task_service import TaskService


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def task_service(db_session: Session, tmp_path: Path) -> TaskService:
    return TaskService(session=db_session, artifact_root=tmp_path / "artifacts")


@pytest.fixture()
def main_agent_service(db_session: Session, tmp_path: Path) -> MainAgentService:
    return MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=RecordingRunner(),
    )


class RecordingRunner:
    def __init__(
        self,
        *,
        classification: IntakeClassificationOutput | None = None,
        intake_error: Exception | None = None,
    ) -> None:
        self.classification = classification or classification_output()
        self.intake_error = intake_error
        self.calls: list[str] = []
        self.intake_contexts: list[AgentToolContext] = []
        self.orchestration_contexts: list[AgentToolContext] = []

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        self.calls.append("intake")
        self.intake_contexts.append(context)
        if self.intake_error is not None:
            raise self.intake_error
        return self.classification

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        self.calls.append("orchestration")
        self.orchestration_contexts.append(context)
        task = TaskRepository(context.session).get_task(run_config.group_id)
        return episode_output_from_task(
            task,
            main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
            summary="Fake orchestration completed.",
        )


class SucceededOutputRunner:
    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        raise AssertionError("intake should not run for pre-classified task")

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        return MainAgentEpisodeOutput(
            task_id=run_config.group_id,
            main_agent_run_id="main-agent-run-001",
            final_task_status="succeeded",
            phase="completed",
            gate_summary=MainAgentGateSummary(
                test_required=False,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=None,
                latest_formal_passed=None,
                has_blocking_failure=False,
                can_finish_as_success=True,
            ),
            next_recommended_action="none",
            summary="Runtime should persist this report before success.",
        )


class TerminalOutputRunner:
    def __init__(self, final_task_status: str) -> None:
        self.final_task_status = final_task_status

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        raise AssertionError("intake should not run for pre-classified task")

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        return MainAgentEpisodeOutput(
            task_id=run_config.group_id,
            main_agent_run_id="main-agent-run-001",
            final_task_status=self.final_task_status,
            phase="completed",
            gate_summary=MainAgentGateSummary(
                test_required=False,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=None,
                latest_formal_passed=None,
                has_blocking_failure=self.final_task_status != "succeeded",
                can_finish_as_success=self.final_task_status == "succeeded",
            ),
            next_recommended_action="none",
            summary=f"Runtime should persist report before {self.final_task_status}.",
        )


class ModelBehaviorErrorRunner:
    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        raise AssertionError("intake should not run for pre-classified task")

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> Any:
        raise ModelBehaviorError("invalid final output")


class ScriptedChatClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        stream: bool = False,
    ) -> dict[str, Any]:
        self.requests.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "stream": stream,
            }
        )
        if not self.responses:
            raise AssertionError("no scripted chat response remains")
        return self.responses.pop(0)


def chat_response(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls or [],
                }
            }
        ]
    }


def streamed_chat_chunks(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = [
            {
                "index": index,
                "id": call["id"],
                "function": {
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                },
            }
            for index, call in enumerate(tool_calls)
        ]
    return [{"choices": [{"delta": delta}]}]


def tool_call(name: str, arguments: dict[str, Any], *, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def raw_tool_call(name: str, arguments: str, *, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def signals(**updates: bool) -> dict[str, bool]:
    values = {
        "has_existing_code": False,
        "has_io_points": False,
        "has_timing_logic": False,
        "has_state_machine": False,
        "has_safety_constraints": False,
        "has_emergency_stop": False,
        "has_interlock": False,
        "has_fault_latching": False,
        "has_mode_switching": False,
        "multi_module": False,
        "requirement_incomplete": False,
    }
    values.update(updates)
    return values


def classification_output(**updates: Any) -> IntakeClassificationOutput:
    values: dict[str, Any] = {
        "normalized_goal": "Create tested motor start/stop PLC logic.",
        "task_type": "new_plc_development",
        "difficulty_level": "L2",
        "difficulty_score": 0.5,
        "difficulty_confidence": 0.85,
        "difficulty_reasons": ["Requires PLC development and validation."],
        "difficulty_signals": signals(has_io_points=True),
        "requires_test": True,
        "requires_formal": False,
        "requires_repair_loop": False,
        "need_clarification": False,
        "clarification_questions": [],
    }
    values.update(updates)
    return IntakeClassificationOutput.model_validate(values)


def create_task(task_service: TaskService) -> str:
    result = task_service.create_task(
        message="Create motor start stop logic with validation.",
        project_context={"target_plc_language": "ST", "target_platform": "Codesys"},
    )
    return result.task.task_id


def artifact_store(db_session: Session, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(session=db_session, artifact_root=tmp_path / "artifacts")


def prepare_report_ready_task(db_session: Session, task_id: str) -> TaskState:
    task = TaskRepository(db_session).get_task(task_id)
    updated = task.model_copy(
        deep=True,
        update={
            "status": TaskStatus.RUNNING.value,
            "phase": TaskPhase.PLANNING.value,
            "task_type": "qa",
            "gates": GateState(
                test_required=False,
                formal_required=False,
                regression_required=False,
                formal_regression_required=False,
                latest_test_passed=None,
                latest_formal_passed=None,
                has_blocking_failure=False,
                can_finish_as_success=True,
            ),
        },
    )
    return TaskRepository(db_session).update_task_state(updated)


def write_artifact(
    db_session: Session,
    tmp_path: Path,
    *,
    task_id: str,
    artifact_type: ArtifactType,
    content: Any,
    summary: str,
    version: int = 1,
) -> str:
    store = artifact_store(db_session, tmp_path)
    artifact = store.write_artifact_content(
        ArtifactContentWrite(
            task_id=task_id,
            artifact_type=artifact_type,
            version=version,
            name=f"{artifact_type.value}_v{version}.txt",
            content=content,
            summary=summary,
            created_by=ArtifactCreator(type=ArtifactCreatorType.RUNTIME),
            mime_type="text/plain",
        )
    ).artifact
    return artifact.artifact_id


def persisted_task(db_session: Session, task_id: str):
    return TaskRepository(db_session).get_task(task_id)


def event_types(db_session: Session, task_id: str) -> list[str]:
    return [event.type for event in EventService(db_session).list_visible_events(task_id)]


def test_state_view_contains_scheduling_facts(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)
    main_agent_service.apply_intake_classification(task_id, classification_output())
    code_artifact_id = write_artifact(
        db_session,
        tmp_path,
        task_id=task_id,
        artifact_type=ArtifactType.PLC_CODE,
        content="FULL_PLC_CODE_SHOULD_NOT_APPEAR",
        summary="Current PLC code summary.",
    )
    task = persisted_task(db_session, task_id)
    failure = Failure(
        failure_id="failure-open-test",
        source="test",
        severity=Severity.BLOCKING,
        title="Emergency stop test failed",
        description="Compact failure description.",
        reproduction=FailureReproduction(steps=["Run emergency stop test."]),
        evidence_artifact_ids=[code_artifact_id],
        status="open",
        created_at=task.created_at,
    )
    updated = task.model_copy(
        deep=True,
        update={
            "runtime_limits": task.runtime_limits.model_copy(
                update={"repair_rounds": 1, "worker_calls_used": 2}
            ),
            "completed_worker_job_ids": ["worker-job-dev", "worker-job-test"],
            "failures": [failure],
        },
    )
    TaskRepository(db_session).update_task_state(updated)

    view = build_state_view(persisted_task(db_session, task_id))

    assert view["task_id"] == task_id
    assert view["user_goal"] == "Create motor start stop logic with validation."
    assert view["normalized_goal"] == "Create tested motor start/stop PLC logic."
    assert view["task_type"] == "new_plc_development"
    assert view["difficulty"]["level"] == "L2"
    assert view["difficulty"]["requires_test"] is True
    assert view["gates"]["test_required"] is True
    assert view["current_artifacts"]["current_code"]["artifact_id"] == code_artifact_id
    assert view["open_failures"][0]["failure_id"] == "failure-open-test"
    assert view["repair_rounds"] == "1/3"
    assert view["runtime_limits"]["worker_calls_used"] == 2
    assert view["completed_worker_job_ids"] == ["worker-job-dev", "worker-job-test"]
    assert view["available_tools"] == list(MAIN_AGENT_TOOL_NAMES)


def test_state_view_excludes_large_artifact_content(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.apply_intake_classification(task_id, classification_output())
    secrets = [
        "FULL_PLC_CODE_BODY_SHOULD_STAY_IN_ARTIFACT",
        "FULL_TEST_REPORT_BODY_SHOULD_STAY_IN_ARTIFACT",
        "FULL_FORMAL_REPORT_BODY_SHOULD_STAY_IN_ARTIFACT",
        "FULL_COUNTEREXAMPLE_BODY_SHOULD_STAY_IN_ARTIFACT",
        "FULL_PATCH_BODY_SHOULD_STAY_IN_ARTIFACT",
        "FULL_WORKER_LOG_BODY_SHOULD_STAY_IN_ARTIFACT",
    ]
    artifact_types = [
        ArtifactType.PLC_CODE,
        ArtifactType.TEST_REPORT,
        ArtifactType.FORMAL_REPORT,
        ArtifactType.COUNTEREXAMPLE,
        ArtifactType.PATCH,
        ArtifactType.WORKER_LOG,
    ]
    for artifact_type, secret in zip(artifact_types, secrets, strict=True):
        write_artifact(
            db_session,
            tmp_path,
            task_id=task_id,
            artifact_type=artifact_type,
            content=secret,
            summary=f"Compact {artifact_type.value} summary.",
        )

    view_json = json.dumps(build_state_view(persisted_task(db_session, task_id)))

    for secret in secrets:
        assert secret not in view_json
    assert "Compact plc_code summary." in view_json
    assert "content_hash" in view_json


def test_start_main_agent_run_persists_trace_and_started_event(
    db_session: Session,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)

    started = main_agent_service.start_main_agent_run(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    started_event = events[-1]

    assert started.trace.openai_trace_id is not None
    assert len(started.trace.main_agent_run_ids) == 1
    assert started.trace.latest_main_agent_run_id == started.trace.main_agent_run_ids[0]
    assert started.started_at is not None
    assert started_event.type == "main_agent.started"
    assert started_event.correlation.openai_trace_id == started.trace.openai_trace_id
    assert (
        started_event.correlation.main_agent_run_id
        == started.trace.latest_main_agent_run_id
    )
    assert started_event.payload["main_agent_run_id"] == started.trace.latest_main_agent_run_id


def test_start_main_agent_run_refreshes_stale_cancelled_task(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'router.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    try:
        with factory() as session:
            task = TaskService(
                session=session,
                artifact_root=artifact_root,
            ).create_task(message="Create motor logic.").task
            session.commit()

        stale_session = factory()
        try:
            service = MainAgentService(
                session=stale_session,
                artifact_root=artifact_root,
                runner=RecordingRunner(),
                checkpoint=stale_session.commit,
            )
            TaskRepository(stale_session).get_task(task.task_id)

            with factory() as cancel_session:
                TaskService(
                    session=cancel_session,
                    artifact_root=artifact_root,
                ).cancel_task(task.task_id)
                cancel_session.commit()

            started = service.start_main_agent_run(task.task_id)
        finally:
            stale_session.close()

        with factory() as session:
            restored = TaskRepository(session).get_task(task.task_id)
            events = EventService(session).list_visible_events(task.task_id)

        assert started.status == "cancelled"
        assert restored.status == "cancelled"
        assert [event.type for event in events] == ["task.created", "task.cancelled"]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_apply_classification_persists_planning_state_and_events(
    db_session: Session,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)

    result = main_agent_service.apply_intake_classification(
        task_id,
        classification_output(requires_test=False),
    )

    assert result.clarification_requested is False
    assert result.task.status == "running"
    assert result.task.phase == "planning"
    assert result.task.normalized_goal == "Create tested motor start/stop PLC logic."
    assert result.task.task_type == "new_plc_development"
    assert result.task.difficulty.level == "L2"
    assert result.task.difficulty.requires_test is True
    assert result.task.gates.test_required is True
    assert event_types(db_session, task_id)[-2:] == [
        "main_agent.decision",
        "task.updated",
    ]


def test_classification_elevates_safety_critical_to_l3_formal(
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)

    result = main_agent_service.apply_intake_classification(
        task_id,
        classification_output(
            difficulty_level="L1",
            difficulty_reasons=["Simple logic, but has emergency stop."],
            difficulty_signals=signals(has_emergency_stop=True),
            requires_test=False,
            requires_formal=False,
        ),
    )

    assert result.task.difficulty.level == "L3"
    assert result.task.difficulty.requires_test is True
    assert result.task.difficulty.requires_formal is True
    assert result.task.gates.test_required is True
    assert result.task.gates.formal_required is True


def test_classification_enforces_repair_loop_for_repair_tasks(
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)

    result = main_agent_service.apply_intake_classification(
        task_id,
        classification_output(
            task_type="repair_existing_code",
            difficulty_level="L1",
            difficulty_signals=signals(has_existing_code=True),
            requires_test=False,
            requires_repair_loop=False,
        ),
    )

    assert result.task.task_type == "repair_existing_code"
    assert result.task.difficulty.requires_repair_loop is True


def test_clarification_classification_waits_for_user_without_workers(
    db_session: Session,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)

    result = main_agent_service.apply_intake_classification(
        task_id,
        classification_output(
            difficulty_level="L1",
            difficulty_reasons=["Missing required platform details."],
            difficulty_signals=signals(requirement_incomplete=True),
            requires_test=False,
            need_clarification=True,
            clarification_questions=[
                {
                    "question": "Which PLC platform should be targeted?",
                    "reason": "The target platform affects code conventions.",
                    "required": True,
                }
            ],
        ),
    )

    assert result.clarification_requested is True
    assert result.task.status == "waiting_user"
    assert result.task.phase == "clarifying"
    assert result.task.unresolved_questions[0].status == "open"
    assert event_types(db_session, task_id)[-2:] == [
        "main_agent.clarification_requested",
        "task.waiting_user",
    ]
    assert row_count(db_session, WorkerJobRow) == 0
    assert not [
        event_type
        for event_type in event_types(db_session, task_id)
        if event_type.startswith("worker.")
    ]


def test_plan_and_finalizing_event_helpers_emit_correlated_events(
    db_session: Session,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    started = main_agent_service.start_main_agent_run(task_id)

    plan_event = main_agent_service.emit_plan_updated(
        task_id,
        summary="Plan now includes development and testing.",
        plan=[{"order": 1, "action": "call_plc_dev"}],
    )
    finalizing_event = main_agent_service.emit_finalizing(
        task_id,
        summary="Running Quality Gate before finish.",
    )

    assert plan_event.type == "main_agent.plan_updated"
    assert finalizing_event.type == "main_agent.finalizing"
    assert plan_event.correlation.openai_trace_id == started.trace.openai_trace_id
    assert finalizing_event.correlation.main_agent_run_id == started.trace.latest_main_agent_run_id


def test_run_episode_with_legacy_runner_classifies_before_orchestration(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    runner = RecordingRunner()
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=runner,
    )
    task_id = create_task(task_service)

    output = service.run_episode(task_id)
    task = persisted_task(db_session, task_id)

    assert runner.calls == ["intake", "orchestration"]
    assert task.status == "running"
    assert task.phase == "planning"
    assert output.task_id == task_id
    assert output.main_agent_run_id == task.trace.latest_main_agent_run_id
    assert row_count(db_session, WorkerJobRow) == 0


def test_tool_loop_runner_plans_and_requests_clarification_without_intake(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    chat_client = ScriptedChatClient(
        [
            chat_response(
                content="I will first capture the plan and check what is missing.",
                tool_calls=[
                    tool_call(
                        "update_plan",
                        {
                            "task_id": task_id,
                            "summary": "Plan created before worker dispatch.",
                            "plan": [
                                {
                                    "order": 1,
                                    "action": "Ask for missing IO details",
                                    "status": "planned",
                                }
                            ],
                            "normalized_goal": "Create motor logic after IO details are confirmed.",
                            "task_type": "new_plc_development",
                            "requires_test": True,
                            "requires_formal": False,
                        },
                        call_id="call-plan",
                    )
                ],
            ),
            chat_response(
                content="The IO list is required before safe worker dispatch.",
                tool_calls=[
                    tool_call(
                        "request_clarification",
                        {
                            "task_id": task_id,
                            "questions": [
                                {
                                    "question": "Which input and output tags should the logic use?",
                                    "reason": "PLC code generation needs concrete IO names.",
                                    "required": True,
                                }
                            ],
                            "rationale_summary": "Missing IO details block safe PLC worker dispatch.",
                        },
                        call_id="call-clarify",
                    )
                ],
            ),
        ]
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        model="fake-main-agent",
        chat_client=chat_client,
        stream=False,
    )

    output = service.run_episode(task_id)
    task = persisted_task(db_session, task_id)
    events = EventService(db_session).list_visible_events(task_id)
    types = [event.type for event in events]

    assert output.final_task_status == "waiting_user"
    assert task.status == "waiting_user"
    assert task.phase == "clarifying"
    assert task.unresolved_questions[0].status == "open"
    assert "main_agent.message" in types
    assert "main_agent.plan_updated" in types
    assert "main_agent.clarification_requested" in types
    assert "task.waiting_user" in types
    assert row_count(db_session, WorkerJobRow) == 0
    assert chat_client.requests[0]["model"] == "fake-main-agent"
    assert chat_client.requests[0]["messages"][0]["role"] == "system"
    assert chat_client.requests[0]["tools"][0]["type"] == "function"
    assert "response_format" not in chat_client.requests[0]


def test_tool_loop_streaming_chunks_reconstruct_message_and_tool_call(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    chat_client = ScriptedChatClient(
        [
            streamed_chat_chunks(
                content="I will capture a public plan.",
                tool_calls=[
                    tool_call(
                        "update_plan",
                        {
                            "task_id": task_id,
                            "summary": "Streaming plan captured.",
                            "plan": [
                                {
                                    "order": 1,
                                    "action": "Clarify platform",
                                    "status": "planned",
                                }
                            ],
                        },
                        call_id="call-stream-plan",
                    )
                ],
            ),
            streamed_chat_chunks(
                content="I need one user detail before dispatching workers.",
                tool_calls=[
                    tool_call(
                        "request_clarification",
                        {
                            "task_id": task_id,
                            "questions": ["Which PLC platform should be targeted?"],
                        },
                        call_id="call-stream-clarify",
                    )
                ],
            ),
        ]
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        model="fake-main-agent",
        chat_client=chat_client,
        stream=True,
    )

    output = service.run_episode(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    message_event = next(event for event in events if event.type == "main_agent.message")

    assert output.final_task_status == "waiting_user"
    assert chat_client.requests[0]["stream"] is True
    assert message_event.payload["content"] == "I will capture a public plan."
    assert "main_agent.plan_updated" in [event.type for event in events]


def test_tool_loop_runner_finalizes_through_report_and_finish_tools(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    prepare_report_ready_task(db_session, task_id)
    chat_client = ScriptedChatClient(
        [
            chat_response(
                content="The task is ready for report-first finalization.",
                tool_calls=[
                    tool_call(
                        "write_final_report",
                        {
                            "task_id": task_id,
                            "final_status": "succeeded",
                            "summary": "Validation passed and delivery is complete.",
                            "plan": [
                                {
                                    "order": 1,
                                    "action": "Finalize ready task",
                                    "status": "completed",
                                }
                            ],
                        },
                        call_id="call-report",
                    )
                ],
            ),
            chat_response(
                content="The report is durable, so I will apply terminal status.",
                tool_calls=[
                    tool_call(
                        "finish_task",
                        {
                            "task_id": task_id,
                            "final_status": "succeeded",
                            "rationale_summary": "Final report exists and Quality Gate allows success.",
                        },
                        call_id="call-finish",
                    )
                ],
            ),
        ]
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        model="fake-main-agent",
        chat_client=chat_client,
        stream=False,
    )

    output = service.run_episode(task_id)
    task = persisted_task(db_session, task_id)
    types = event_types(db_session, task_id)

    assert output.final_task_status == "succeeded"
    assert task.status == "succeeded"
    assert task.current_artifacts.final_report is not None
    assert types.index("main_agent.completed") < types.index("task.succeeded")
    assert any(
        message["role"] == "tool" and message["tool_call_id"] == "call-report"
        for message in chat_client.requests[1]["messages"]
    )


def test_tool_loop_records_malformed_tool_arguments_and_continues(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    chat_client = ScriptedChatClient(
        [
            chat_response(
                content="I will update the plan.",
                tool_calls=[
                    raw_tool_call(
                        "update_plan",
                        "{",
                        call_id="call-bad-plan",
                    )
                ],
            ),
            chat_response(
                content="The previous tool arguments were invalid, so I will pause safely.",
                tool_calls=[
                    tool_call(
                        "request_clarification",
                        {
                            "task_id": task_id,
                            "questions": ["Which PLC platform should be targeted?"],
                        },
                        call_id="call-clarify-after-error",
                    )
                ],
            ),
        ]
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        model="fake-main-agent",
        chat_client=chat_client,
        stream=False,
    )

    output = service.run_episode(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    failed_tool_result = next(
        event
        for event in events
        if event.type == "main_agent.tool_result"
        and event.payload["tool_name"] == "update_plan"
    )

    assert output.final_task_status == "waiting_user"
    assert failed_tool_result.payload["status"] == "failed"
    assert failed_tool_result.payload["details"]["error"]["error_code"] == (
        "invalid_tool_arguments"
    )
    assert "task.succeeded" not in [event.type for event in events]


def test_tool_loop_runner_max_turns_records_failure_without_success(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    chat_client = ScriptedChatClient(
        [
            chat_response(content="I am still considering the next public step."),
        ]
    )
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        model="fake-main-agent",
        max_turns=1,
        chat_client=chat_client,
        stream=False,
    )

    output = service.run_episode(task_id)
    task = persisted_task(db_session, task_id)
    types = event_types(db_session, task_id)

    assert output.error_code == "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    assert task.status == "failed"
    assert "main_agent.message" in types
    assert "task.succeeded" not in types
    assert types[-1] == "task.failed"


def test_run_episode_checkpoint_callback_runs_at_visible_milestones(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    checkpoints: list[str] = []
    runner = RecordingRunner()
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=runner,
        checkpoint=lambda: checkpoints.append("checkpoint"),
    )
    task_id = create_task(task_service)

    service.run_episode(task_id)

    assert runner.calls == ["intake", "orchestration"]
    assert len(checkpoints) >= 5
    assert set(checkpoints) == {"checkpoint"}


def test_run_episode_persists_report_before_terminal_success(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    prepare_report_ready_task(db_session, task_id)
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=SucceededOutputRunner(),
    )

    output = service.run_episode(task_id)
    updated = TaskRepository(db_session).get_task(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    event_types = [event.type for event in events]
    artifact_rows = list(
        db_session.execute(select(ArtifactRow).where(ArtifactRow.task_id == task_id))
        .scalars()
        .all()
    )

    assert output.final_task_status == "succeeded"
    assert updated.status == "succeeded"
    assert event_types.index("main_agent.completed") < event_types.index(
        "task.succeeded"
    )
    assert {"final_report", "main_agent_log"} <= {row.type for row in artifact_rows}
    assert updated.current_artifacts.final_report is not None


@pytest.mark.parametrize("final_status", ["partial_failed", "failed"])
def test_run_episode_persists_report_before_non_success_terminal_status(
    final_status: str,
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    prepare_report_ready_task(db_session, task_id)
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=TerminalOutputRunner(final_status),
    )

    output = service.run_episode(task_id)
    updated = TaskRepository(db_session).get_task(task_id)
    events = EventService(db_session).list_visible_events(task_id)
    event_types = [event.type for event in events]

    assert output.final_task_status == final_status
    assert updated.status == final_status
    assert updated.current_artifacts.final_report is not None
    assert event_types.index("main_agent.completed") < event_types.index(
        f"task.{final_status}"
    )
    report = ArtifactStore(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).read_artifact_content(updated.current_artifacts.final_report.artifact_id)
    report_content = json.loads(report.content)
    assert report_content["report_version"] == 1
    assert report_content["final_task_status"] == final_status


def test_run_episode_report_persistence_failure_does_not_mark_success(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    prepare_report_ready_task(db_session, task_id)
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=SucceededOutputRunner(),
    )

    def fail_write(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("report write failed")

    monkeypatch.setattr(MainAgentObservabilityRecorder, "write_final_report", fail_write)

    with pytest.raises(RuntimeError, match="report write failed"):
        service.run_episode(task_id)

    updated = TaskRepository(db_session).get_task(task_id)
    event_types = [
        event.type for event in EventService(db_session).list_visible_events(task_id)
    ]

    assert updated.status == "running"
    assert "main_agent.completed" not in event_types
    assert "task.succeeded" not in event_types


def test_run_episode_model_behavior_error_does_not_write_final_report_or_terminalize(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    prepare_report_ready_task(db_session, task_id)
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=ModelBehaviorErrorRunner(),
    )

    output = service.run_episode(task_id)
    updated = TaskRepository(db_session).get_task(task_id)
    event_types = [
        event.type for event in EventService(db_session).list_visible_events(task_id)
    ]
    artifact_types = [
        row.type
        for row in db_session.execute(
            select(ArtifactRow).where(ArtifactRow.task_id == task_id)
        ).scalars()
    ]

    assert output.error_code == "MAIN_AGENT_MODEL_BEHAVIOR_ERROR"
    assert updated.status == "running"
    assert updated.current_artifacts.final_report is None
    assert "final_report" not in artifact_types
    assert "main_agent.completed" not in event_types
    assert "task.succeeded" not in event_types


def test_run_episode_skips_intake_for_classified_running_task(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    main_agent_service.start_main_agent_run(task_id)
    main_agent_service.apply_intake_classification(task_id, classification_output())
    runner = RecordingRunner()
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=runner,
    )

    output = service.run_episode(task_id)

    assert runner.calls == ["orchestration"]
    assert output.final_task_status == "running"


def test_run_episode_does_not_rerun_terminal_tasks(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    task = persisted_task(db_session, task_id)
    terminal = task.model_copy(
        deep=True,
        update={"status": TaskStatus.SUCCEEDED.value, "phase": TaskPhase.COMPLETED.value},
    )
    TaskRepository(db_session).update_task_state(terminal)
    runner = RecordingRunner()
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=runner,
    )

    output = service.run_episode(task_id)

    assert runner.calls == []
    assert output.final_task_status == "succeeded"
    assert persisted_task(db_session, task_id).status == "succeeded"
    assert event_types(db_session, task_id) == ["task.created"]


def test_run_episode_missing_task_has_no_side_effects(
    db_session: Session,
    tmp_path: Path,
) -> None:
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=RecordingRunner(),
    )

    with pytest.raises(RepositoryNotFoundError):
        service.run_episode("task-missing")

    assert row_count(db_session, TaskRow) == 0
    assert row_count(db_session, EventRow) == 0
    assert row_count(db_session, ArtifactRow) == 0
    assert row_count(db_session, WorkerJobRow) == 0


def test_run_episode_records_max_turns_error_without_success(
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
) -> None:
    task_id = create_task(task_service)
    runner = RecordingRunner(intake_error=MaxTurnsExceeded("too many turns"))
    service = MainAgentService(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        runner=runner,
    )

    output = service.run_episode(task_id)
    task = persisted_task(db_session, task_id)
    events = EventService(db_session).list_visible_events(task_id)

    assert output.error_code == "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    assert task.status == "failed"
    assert task.phase == "completed"
    assert task.completed_at is not None
    assert task.current_artifacts.final_report is not None
    assert [event.type for event in events[-3:]] == [
        "main_agent.decision",
        "main_agent.completed",
        "task.failed",
    ]
    assert events[-3].severity == "error"
    assert events[-2].payload["final_report_artifact_id"] == (
        task.current_artifacts.final_report.artifact_id
    )
    assert events[-1].type == "task.failed"
    report = ArtifactStore(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
    ).read_artifact_content(task.current_artifacts.final_report.artifact_id)
    report_content = json.loads(report.content)
    assert report_content["final_task_status"] == "failed"
    assert report_content["main_agent_output_summary"]["error_code"] == (
        "MAIN_AGENT_MAX_TURNS_EXCEEDED"
    )


def test_production_agent_builders_and_run_config(
    db_session: Session,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    started = main_agent_service.start_main_agent_run(task_id)
    intake_agent = build_intake_agent(model="gpt-4.1-mini")
    orchestration_agent = build_orchestration_agent(model="gpt-4.1-mini")
    run_config = build_run_config(started, phase="intake")

    assert intake_agent.name == "Router Intake Classifier"
    assert intake_agent.output_type.output_type is IntakeClassificationOutput
    assert intake_agent.output_type.is_strict_json_schema() is False
    assert orchestration_agent.name == "Router Main Agent"
    assert orchestration_agent.output_type.is_strict_json_schema() is False
    assert [tool.name for tool in orchestration_agent.tools] == list(MAIN_AGENT_TOOL_NAMES)
    assert run_config.workflow_name == "Router Main Agent"
    assert run_config.trace_id == started.trace.openai_trace_id
    assert run_config.group_id == task_id
    assert run_config.trace_metadata["main_agent_run_id"] == started.trace.latest_main_agent_run_id


def test_openai_runner_uses_streaming_when_observability_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    tmp_path: Path,
    task_service: TaskService,
    main_agent_service: MainAgentService,
) -> None:
    task_id = create_task(task_service)
    started = main_agent_service.start_main_agent_run(task_id)
    output = episode_output_from_task(
        started,
        main_agent_run_id=started.trace.latest_main_agent_run_id or "not-started",
        summary="Fake streamed orchestration completed.",
    )

    class FakeStreamingResult:
        def __init__(self, hooks: Any) -> None:
            self.hooks = hooks

        async def stream_events(self) -> Any:
            await self.hooks.on_llm_start(None, None, None, [])
            if False:
                yield None

        def final_output_as(
            self,
            cls: type[Any],
            raise_if_incorrect_type: bool = False,
        ) -> Any:
            return output

    class FakeRunner:
        run_streamed_called = False

        @classmethod
        def run_streamed(cls, *args: Any, **kwargs: Any) -> FakeStreamingResult:
            cls.run_streamed_called = True
            return FakeStreamingResult(kwargs["hooks"])

    recorder = MainAgentObservabilityRecorder(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        task_id=task_id,
        main_agent_run_id=started.trace.latest_main_agent_run_id,
        openai_trace_id=started.trace.openai_trace_id,
    )
    context = AgentToolContext(
        session=db_session,
        artifact_root=tmp_path / "artifacts",
        observability_recorder=recorder,
    )
    monkeypatch.setattr(main_agent_module, "Runner", FakeRunner)

    streamed = OpenAIAgentsRunner().run_orchestration(
        agent=object(),
        input_text="state",
        context=context,
        max_turns=3,
        run_config=object(),
    )
    events = EventService(db_session).list_visible_events(task_id)

    assert FakeRunner.run_streamed_called is True
    assert streamed.summary == "Fake streamed orchestration completed."
    assert [event.type for event in events][-1] == "main_agent.turn_started"


def row_count(db_session: Session, row_type: type[Any]) -> int:
    return db_session.execute(select(func.count()).select_from(row_type)).scalar_one()
