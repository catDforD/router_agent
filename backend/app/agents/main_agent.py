"""Main Agent service for Router task episodes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from pydantic import JsonValue
from sqlalchemy.orm import Session

try:
    from agents import (
        Agent,
        AgentOutputSchema,
        MaxTurnsExceeded,
        ModelBehaviorError,
        RunConfig,
        Runner,
        gen_trace_id,
    )
    from agents.lifecycle import RunHooksBase

    AGENTS_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - SDK is a runtime dependency.
    Agent = Any  # type: ignore[assignment]
    AgentOutputSchema = Any  # type: ignore[assignment]
    RunHooksBase = object  # type: ignore[assignment]
    RunConfig = Any  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    AGENTS_SDK_AVAILABLE = False

    class MaxTurnsExceeded(Exception):  # type: ignore[no-redef]
        pass

    class ModelBehaviorError(Exception):  # type: ignore[no-redef]
        pass

    def gen_trace_id() -> str:  # type: ignore[no-redef]
        from uuid import uuid4

        return f"trace_{uuid4().hex}"

from app.agents.instructions import (
    INTAKE_AGENT_NAME,
    ORCHESTRATION_AGENT_NAME,
    build_intake_instructions,
    build_orchestration_instructions,
    build_state_view_prompt,
)
from app.agents.chat_completions import (
    MainAgentChatClient,
    MainAgentProviderConfigurationError,
    MainAgentProviderError,
    OpenAICompatibleChatClient,
)
from app.agents.observability import MainAgentObservabilityRecorder
from app.agents.output_schema import (
    IntakeClassificationOutput,
    MainAgentArtifactReference,
    MainAgentDecision,
    MainAgentEpisodeOutput,
    MainAgentGateSummary,
)
from app.agents.tools import (
    AgentToolContext,
    AgentToolResult,
    ToolError,
    ToolStatus,
    call_main_agent_tool,
    get_main_agent_tool_specs,
    get_main_agent_tools,
)
from app.core.ids import new_event_id, prefixed_id
from app.core.logging import log_with_context
from app.core.time import utc_now
from app.mcp.mock_worker import DEFAULT_MOCK_SCENARIO
from app.models.router_schema import (
    ArtifactRef,
    ClarificationQuestion,
    CurrentArtifacts,
    DifficultyLevel,
    DifficultyProfile,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    Failure,
    GateState,
    RouterEvent,
    TaskPhase,
    TaskState,
    TaskStatus,
    TaskTrace,
    TaskType,
)
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.scheduler_guard import SchedulerGuardViolation, validate_finish_task


DEFAULT_MAIN_AGENT_MAX_TURNS = 20
LOGGER = logging.getLogger(__name__)
TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.PARTIAL_FAILED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}
TERMINAL_EVENT_BY_STATUS = {
    TaskStatus.SUCCEEDED.value: EventType.TASK_SUCCEEDED,
    TaskStatus.PARTIAL_FAILED.value: EventType.TASK_PARTIAL_FAILED,
    TaskStatus.FAILED.value: EventType.TASK_FAILED,
    TaskStatus.CANCELLED.value: EventType.TASK_CANCELLED,
}
SAFETY_SIGNAL_FIELDS = (
    "has_safety_constraints",
    "has_emergency_stop",
    "has_interlock",
    "has_fault_latching",
    "has_mode_switching",
    "has_state_machine",
)
DIFFICULTY_RANK = {
    DifficultyLevel.L0.value: 0,
    DifficultyLevel.L1.value: 1,
    DifficultyLevel.L2.value: 2,
    DifficultyLevel.L3.value: 3,
    DifficultyLevel.L4.value: 4,
}
DIFFICULTY_BY_RANK = {
    rank: level
    for level, rank in DIFFICULTY_RANK.items()
}
MAIN_AGENT_TOOL_NAMES = (
    "update_plan",
    "request_clarification",
    "call_plc_dev",
    "call_plc_test",
    "call_plc_formal",
    "call_plc_repair",
    "run_parallel_workers",
    "read_artifact",
    "run_quality_gate",
    "write_final_report",
    "finish_task",
)


class MainAgentServiceError(Exception):
    """Base class for Main Agent service failures."""


class MainAgentRunnerUnavailableError(MainAgentServiceError):
    """Raised when production SDK execution is requested without the SDK."""


class MainAgentRunner(Protocol):
    """Runner boundary used by production SDK calls and deterministic tests."""

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> MainAgentEpisodeOutput:
        """Return structured orchestration episode output."""


@dataclass(frozen=True)
class ToolLoopAgent:
    """Minimal agent descriptor for the Chat Completions tool-loop runner."""

    name: str
    instructions: str
    model: str | None = None


@dataclass(frozen=True)
class ToolLoopRunConfig:
    """Run metadata shape shared with deterministic runner tests."""

    workflow_name: str
    trace_id: str | None
    group_id: str
    trace_metadata: dict[str, Any]


@dataclass(frozen=True)
class _ChatToolCall:
    tool_call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class _AssistantTurn:
    content: str | None
    tool_calls: list[_ChatToolCall]


class OpenAICompatibleToolLoopRunner:
    """Production Main Agent runner using OpenAI-compatible Chat Completions."""

    uses_tool_loop_side_effects = True

    def __init__(
        self,
        *,
        chat_client: MainAgentChatClient | None = None,
        stream: bool = True,
    ) -> None:
        self.chat_client = chat_client or OpenAICompatibleChatClient.from_settings()
        self.stream = stream

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> MainAgentEpisodeOutput:
        model = getattr(agent, "model", None)
        if not model:
            raise MainAgentProviderConfigurationError(
                "MAIN_AGENT_MODEL is required for Main Agent execution"
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": getattr(agent, "instructions", "")},
            {"role": "user", "content": input_text},
        ]
        tools = get_main_agent_tool_specs()
        recorder = context.observability_recorder
        task_id = run_config.group_id

        for _ in range(max_turns):
            turn_index = (
                recorder.start_turn(phase="orchestration")
                if recorder is not None
                else None
            )
            assistant_turn = self._complete_turn(
                messages=messages,
                tools=tools,
                model=model,
                recorder=recorder,
            )
            assistant_message = _assistant_message_for_history(assistant_turn)
            messages.append(assistant_message)

            if assistant_turn.content and recorder is not None:
                recorder.record_message(
                    content=assistant_turn.content,
                    turn_index=turn_index,
                )

            for tool_call in assistant_turn.tool_calls:
                tool_result = _execute_tool_call(
                    context=context,
                    task_id=task_id,
                    tool_call=tool_call,
                    recorder=recorder,
                    turn_index=turn_index,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.tool_call_id,
                        "content": _tool_result_content(tool_result),
                    }
                )
                current = TaskRepository(context.session).get_task(task_id)
                if _is_terminal(current) or _value(current.status) == (
                    TaskStatus.WAITING_USER.value
                ):
                    return episode_output_from_task(
                        current,
                        main_agent_run_id=(
                            current.trace.latest_main_agent_run_id or "not-started"
                        ),
                        summary=_episode_summary_from_tool_result(tool_result),
                    )

        if recorder is not None:
            recorder.record_error(
                error_code="MAIN_AGENT_MAX_TURNS_EXCEEDED",
                message=f"Main Agent exceeded max turn limit ({max_turns}).",
            )
        raise MaxTurnsExceeded(f"Main Agent exceeded max turn limit ({max_turns})")

    def _complete_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        recorder: MainAgentObservabilityRecorder | None,
    ) -> _AssistantTurn:
        try:
            response = self.chat_client.complete(
                messages=messages,
                tools=tools,
                model=model,
                stream=self.stream,
            )
        except MainAgentProviderError:
            if not self.stream:
                raise
            if recorder is not None:
                recorder.record_error(
                    error_code="MAIN_AGENT_STREAMING_FALLBACK",
                    message=(
                        "Streaming Main Agent request failed; retrying the same "
                        "turn without streaming."
                    ),
                )
            response = self.chat_client.complete(
                messages=messages,
                tools=tools,
                model=model,
                stream=False,
            )

        return _assistant_turn_from_response(response)


class OpenAIAgentsRunner:
    """Production runner backed by the OpenAI Agents SDK."""

    def run_intake(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> IntakeClassificationOutput:
        if Runner is None:
            raise MainAgentRunnerUnavailableError("OpenAI Agents SDK is not available")
        result = Runner.run_sync(
            agent,
            input_text,
            context=context,
            max_turns=max_turns,
            run_config=run_config,
        )
        return result.final_output_as(IntakeClassificationOutput, raise_if_incorrect_type=True)

    def run_orchestration(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> MainAgentEpisodeOutput:
        if Runner is None:
            raise MainAgentRunnerUnavailableError("OpenAI Agents SDK is not available")
        if context.observability_recorder is not None:
            return self._run_orchestration_streamed(
                agent=agent,
                input_text=input_text,
                context=context,
                max_turns=max_turns,
                run_config=run_config,
            )
        result = Runner.run_sync(
            agent,
            input_text,
            context=context,
            max_turns=max_turns,
            run_config=run_config,
        )
        return result.final_output_as(MainAgentEpisodeOutput, raise_if_incorrect_type=True)

    def _run_orchestration_streamed(
        self,
        *,
        agent: Any,
        input_text: str,
        context: AgentToolContext,
        max_turns: int,
        run_config: Any,
    ) -> MainAgentEpisodeOutput:
        async def run_streamed() -> MainAgentEpisodeOutput:
            result = Runner.run_streamed(
                agent,
                input_text,
                context=context,
                max_turns=max_turns,
                run_config=run_config,
                hooks=_ObservabilityRunHooks(context.observability_recorder),
            )
            async for _event in result.stream_events():
                # Tool calls/results are persisted by AgentToolService. Draining the
                # SDK stream keeps the model run live without exposing raw SDK events.
                pass
            return result.final_output_as(
                MainAgentEpisodeOutput,
                raise_if_incorrect_type=True,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(run_streamed())
        raise MainAgentServiceError(
            "streamed OpenAI Agents runner cannot run inside an active event loop"
        )


class _ObservabilityRunHooks(RunHooksBase):  # type: ignore[misc, valid-type]
    """SDK lifecycle hook adapter for Router observability."""

    def __init__(self, recorder: Any | None) -> None:
        self.recorder = recorder

    async def on_llm_start(
        self,
        context: Any,
        agent: Any,
        system_prompt: str | None,
        input_items: list[Any],
    ) -> None:
        if self.recorder is not None:
            self.recorder.start_turn(phase="orchestration")


@dataclass(frozen=True)
class ClassificationApplyResult:
    task: TaskState
    classification: IntakeClassificationOutput
    clarification_requested: bool


class MainAgentService:
    """Coordinates one Main Agent episode over persisted Router state."""

    def __init__(
        self,
        *,
        session: Session,
        artifact_root: Path,
        mcp_mode: str = "mock",
        mock_scenario: str = DEFAULT_MOCK_SCENARIO,
        model: str | None = None,
        max_turns: int = DEFAULT_MAIN_AGENT_MAX_TURNS,
        provider: str = "openai_compatible",
        stream: bool = True,
        chat_client: MainAgentChatClient | None = None,
        runner: MainAgentRunner | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.artifact_root = artifact_root
        self.mcp_mode = mcp_mode
        self.mock_scenario = mock_scenario
        self.model = model
        self.max_turns = max_turns
        self.provider = provider
        self.stream = stream
        self.runner = runner or _default_runner(
            provider=provider,
            stream=stream,
            chat_client=chat_client,
        )
        self.checkpoint = checkpoint
        self.task_repository = TaskRepository(session)
        self.event_service = EventService(session)

    def run_episode(self, task_id: str) -> MainAgentEpisodeOutput:
        task = self._fresh_task(task_id)
        if _is_terminal(task):
            return episode_output_from_task(
                task,
                main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
                summary="Terminal task was not re-run.",
            )

        started = self.start_main_agent_run(task_id)
        if _is_terminal(started):
            return episode_output_from_task(
                started,
                main_agent_run_id=started.trace.latest_main_agent_run_id or "not-started",
                summary="Terminal task was not re-run.",
            )
        log_with_context(
            LOGGER,
            logging.INFO,
            "Main Agent episode started",
            task_id=task_id,
            openai_trace_id=started.trace.openai_trace_id,
            main_agent_run_id=started.trace.latest_main_agent_run_id,
        )

        context = AgentToolContext(
            session=self.session,
            artifact_root=self.artifact_root,
            mcp_mode=self.mcp_mode,
            mock_scenario=self.mock_scenario,
            report_first_finalization=True,
            checkpoint=self.checkpoint,
        )
        recorder = MainAgentObservabilityRecorder(
            session=self.session,
            artifact_root=self.artifact_root,
            task_id=task_id,
            openai_trace_id=started.trace.openai_trace_id,
            main_agent_run_id=started.trace.latest_main_agent_run_id,
            checkpoint=self.checkpoint,
        )
        context = replace(context, observability_recorder=recorder)

        try:
            current = started
            legacy_structured_runner = _uses_legacy_structured_runner(self.runner)
            if legacy_structured_runner and _needs_classification(current):
                current = self._run_and_apply_intake(current, context).task
                if current.status == TaskStatus.WAITING_USER.value:
                    output = episode_output_from_task(
                        current,
                        main_agent_run_id=current.trace.latest_main_agent_run_id
                        or "not-started",
                        summary="Task is waiting for user clarification.",
                    )
                    log_with_context(
                        LOGGER,
                        logging.INFO,
                        "Main Agent episode paused",
                        task_id=task_id,
                        openai_trace_id=current.trace.openai_trace_id,
                        main_agent_run_id=current.trace.latest_main_agent_run_id,
                        status=current.status,
                    )
                    return output

            if _is_terminal(current):
                output = episode_output_from_task(
                    current,
                    main_agent_run_id=current.trace.latest_main_agent_run_id
                    or "not-started",
                    summary="Task reached a terminal state during intake.",
                )
                log_with_context(
                    LOGGER,
                    logging.INFO,
                    "Main Agent episode completed during intake",
                    task_id=task_id,
                    openai_trace_id=current.trace.openai_trace_id,
                    main_agent_run_id=current.trace.latest_main_agent_run_id,
                    status=current.status,
                )
                return output

            state_view = build_state_view(current)
            output = self.runner.run_orchestration(
                agent=(
                    build_orchestration_agent(model=self.model)
                    if legacy_structured_runner
                    else build_tool_loop_agent(model=self.model)
                ),
                input_text=build_state_view_prompt(state_view),
                context=context,
                max_turns=self.max_turns,
                run_config=(
                    build_run_config(current, phase="orchestration")
                    if legacy_structured_runner
                    else build_tool_loop_run_config(current, phase="orchestration")
                ),
            )
            persisted_output = (
                output
                if getattr(self.runner, "uses_tool_loop_side_effects", False)
                else self._persist_orchestration_output(output, recorder)
            )
            log_with_context(
                LOGGER,
                logging.INFO,
                "Main Agent episode completed",
                task_id=task_id,
                openai_trace_id=current.trace.openai_trace_id,
                main_agent_run_id=current.trace.latest_main_agent_run_id,
                final_task_status=persisted_output.final_task_status,
            )
            return persisted_output
        except MaxTurnsExceeded as exc:
            return self._record_agent_error(
                task_id=task_id,
                error_code="MAIN_AGENT_MAX_TURNS_EXCEEDED",
                message=str(exc),
                terminal_status=TaskStatus.FAILED.value,
            )
        except ModelBehaviorError as exc:
            return self._record_agent_error(
                task_id=task_id,
                error_code="MAIN_AGENT_MODEL_BEHAVIOR_ERROR",
                message=str(exc),
            )
        except MainAgentProviderConfigurationError as exc:
            return self._record_agent_error(
                task_id=task_id,
                error_code="MAIN_AGENT_PROVIDER_CONFIGURATION_ERROR",
                message=str(exc),
            )
        except MainAgentProviderError as exc:
            return self._record_agent_error(
                task_id=task_id,
                error_code="MAIN_AGENT_PROVIDER_ERROR",
                message=str(exc),
            )

    def start_main_agent_run(self, task_id: str) -> TaskState:
        task = self._fresh_task_for_update(task_id)
        if _is_terminal(task):
            return task

        now = utc_now()
        openai_trace_id = task.trace.openai_trace_id or gen_trace_id()
        main_agent_run_id = new_main_agent_run_id()
        main_agent_run_ids = [
            *task.trace.main_agent_run_ids,
            main_agent_run_id,
        ]
        updated = task.model_copy(
            deep=True,
            update={
                "trace": TaskTrace(
                    openai_trace_id=openai_trace_id,
                    main_agent_run_ids=main_agent_run_ids,
                    latest_main_agent_run_id=main_agent_run_id,
                ),
                "started_at": task.started_at or now,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_STARTED,
                title="Main Agent started",
                message="Main Agent episode started.",
                openai_trace_id=openai_trace_id,
                main_agent_run_id=main_agent_run_id,
                payload={
                    "task_id": task_id,
                    "main_agent_run_id": main_agent_run_id,
                    "phase": _value(task.phase),
                    "status": _value(task.status),
                },
                created_at=now,
            )
        )
        self._checkpoint()
        return self._fresh_task(task_id)

    def apply_intake_classification(
        self,
        task_id: str,
        classification: IntakeClassificationOutput,
    ) -> ClassificationApplyResult:
        normalized = normalize_classification(classification)
        task = self._fresh_task_for_update(task_id)
        if _is_terminal(task):
            return ClassificationApplyResult(
                task=task,
                classification=normalized,
                clarification_requested=False,
            )

        now = utc_now()
        questions = (
            [
                ClarificationQuestion(
                    question_id=prefixed_id("question"),
                    question=question.question,
                    reason=question.reason,
                    required=question.required,
                    status="open",
                    asked_at=now,
                )
                for question in normalized.clarification_questions
            ]
            if normalized.need_clarification
            else []
        )
        difficulty = DifficultyProfile(
            level=normalized.difficulty_level,
            score=normalized.difficulty_score,
            confidence=normalized.difficulty_confidence,
            reasons=normalized.difficulty_reasons,
            signals=normalized.difficulty_signals,
            requires_test=normalized.requires_test,
            requires_formal=normalized.requires_formal,
            requires_repair_loop=normalized.requires_repair_loop,
            need_clarification=normalized.need_clarification,
        )
        gates = GateState(
            test_required=normalized.requires_test,
            formal_required=normalized.requires_formal,
            regression_required=task.gates.regression_required,
            formal_regression_required=task.gates.formal_regression_required,
            latest_test_passed=task.gates.latest_test_passed,
            latest_formal_passed=task.gates.latest_formal_passed,
            has_blocking_failure=task.gates.has_blocking_failure,
            can_finish_as_success=False,
        )
        status = (
            TaskStatus.WAITING_USER.value
            if normalized.need_clarification
            else TaskStatus.RUNNING.value
        )
        phase = (
            TaskPhase.CLARIFYING.value
            if normalized.need_clarification
            else TaskPhase.PLANNING.value
        )
        updated = task.model_copy(
            deep=True,
            update={
                "normalized_goal": normalized.normalized_goal,
                "task_type": normalized.task_type,
                "difficulty": difficulty,
                "gates": gates,
                "status": status,
                "phase": phase,
                "unresolved_questions": questions or task.unresolved_questions,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_DECISION,
                title="Main Agent classified task",
                message="Main Agent intake classification was applied.",
                openai_trace_id=updated.trace.openai_trace_id,
                main_agent_run_id=updated.trace.latest_main_agent_run_id,
                payload={
                    "task_id": task_id,
                    "task_type": _value(normalized.task_type),
                    "difficulty_level": _value(normalized.difficulty_level),
                    "requires_test": normalized.requires_test,
                    "requires_formal": normalized.requires_formal,
                    "need_clarification": normalized.need_clarification,
                },
                created_at=now,
            )
        )
        if normalized.need_clarification:
            self.event_service.append_event(
                build_main_agent_event(
                    task_id=task_id,
                    event_type=EventType.MAIN_AGENT_CLARIFICATION_REQUESTED,
                    title="Main Agent requested clarification",
                    message="Main Agent paused execution for user clarification.",
                    openai_trace_id=updated.trace.openai_trace_id,
                    main_agent_run_id=updated.trace.latest_main_agent_run_id,
                    payload={
                        "task_id": task_id,
                        "question_ids": [question.question_id for question in questions],
                    },
                    created_at=now,
                )
            )
            self.event_service.append_event(
                build_task_event(
                    task_id=task_id,
                    event_type=EventType.TASK_WAITING_USER,
                    title="Task waiting for user",
                    message="The task needs user clarification before workers can run.",
                    openai_trace_id=updated.trace.openai_trace_id,
                    main_agent_run_id=updated.trace.latest_main_agent_run_id,
                    payload={
                        "task_id": task_id,
                        "status": TaskStatus.WAITING_USER.value,
                        "phase": TaskPhase.CLARIFYING.value,
                        "question_ids": [question.question_id for question in questions],
                    },
                    created_at=now,
                )
            )
        else:
            self.event_service.append_event(
                build_task_event(
                    task_id=task_id,
                    event_type=EventType.TASK_UPDATED,
                    title="Task classified",
                    message="The task was classified and is ready for planning.",
                    openai_trace_id=updated.trace.openai_trace_id,
                    main_agent_run_id=updated.trace.latest_main_agent_run_id,
                    payload={
                        "task_id": task_id,
                        "status": TaskStatus.RUNNING.value,
                        "phase": TaskPhase.PLANNING.value,
                        "task_type": _value(normalized.task_type),
                        "difficulty_level": _value(normalized.difficulty_level),
                    },
                    created_at=now,
                )
            )

        self._checkpoint()
        persisted = self.task_repository.get_task(task_id)
        return ClassificationApplyResult(
            task=persisted,
            classification=normalized,
            clarification_requested=normalized.need_clarification,
        )

    def emit_plan_updated(
        self,
        task_id: str,
        *,
        summary: str,
        plan: list[dict[str, JsonValue]] | None = None,
    ) -> RouterEvent:
        task = self.task_repository.get_task(task_id)
        event = self.event_service.append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_PLAN_UPDATED,
                title="Main Agent plan updated",
                message=summary,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id, "plan": plan or []},
                created_at=utc_now(),
            )
        )
        self._checkpoint()
        return event

    def emit_finalizing(self, task_id: str, *, summary: str) -> RouterEvent:
        task = self.task_repository.get_task(task_id)
        event = self.event_service.append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_FINALIZING,
                title="Main Agent finalizing",
                message=summary,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                payload={"task_id": task_id},
                created_at=utc_now(),
            )
        )
        self._checkpoint()
        return event

    def _run_and_apply_intake(
        self,
        task: TaskState,
        context: AgentToolContext,
    ) -> ClassificationApplyResult:
        classification = self.runner.run_intake(
            agent=build_intake_agent(model=self.model),
            input_text=build_state_view_prompt(build_state_view(task)),
            context=context,
            max_turns=self.max_turns,
            run_config=build_run_config(task, phase="intake"),
        )
        return self.apply_intake_classification(task.task_id, classification)

    def _persist_orchestration_output(
        self,
        output: MainAgentEpisodeOutput,
        recorder: MainAgentObservabilityRecorder,
    ) -> MainAgentEpisodeOutput:
        try:
            final_report = recorder.write_final_report(output)
            replay_log = recorder.write_replay_log(final_output=output)
            recorder.record_completed(
                output=output,
                final_report=final_report,
                replay_log=replay_log,
            )
            self._apply_output_terminal_status(output)
        except SchedulerGuardViolation as exc:
            recorder.record_error(
                error_code=_value(exc.code),
                message=exc.message,
                details=exc.details,
            )
        except Exception as exc:
            recorder.record_error(
                error_code=type(exc).__name__,
                message=str(exc),
            )
            raise
        return output

    def _apply_output_terminal_status(self, output: MainAgentEpisodeOutput) -> TaskState:
        final_status = _value(output.final_task_status)
        task = self._fresh_task_for_update(output.task_id)
        if _is_terminal(task) or final_status not in TERMINAL_EVENT_BY_STATUS:
            return task

        validate_finish_task(task, final_status)
        now = utc_now()
        updated = task.model_copy(
            deep=True,
            update={
                "status": final_status,
                "phase": TaskPhase.COMPLETED.value,
                "updated_at": now,
                "completed_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self.event_service.append_event(
            build_task_event(
                task_id=task.task_id,
                event_type=TERMINAL_EVENT_BY_STATUS[final_status],
                title=_terminal_event_title(final_status),
                message=f"The task was marked {final_status}.",
                openai_trace_id=updated.trace.openai_trace_id,
                main_agent_run_id=updated.trace.latest_main_agent_run_id,
                payload={
                    "task_id": task.task_id,
                    "status": final_status,
                },
                created_at=now,
            )
        )
        self._checkpoint()
        return self._fresh_task(output.task_id)

    def _record_agent_error(
        self,
        *,
        task_id: str,
        error_code: str,
        message: str,
        terminal_status: str | None = None,
    ) -> MainAgentEpisodeOutput:
        task = self._fresh_task(task_id)
        if _is_terminal(task):
            log_with_context(
                LOGGER,
                logging.ERROR,
                "Main Agent error observed after terminal task",
                task_id=task_id,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                error_code=error_code,
                error_message=message,
            )
            return episode_output_from_task(
                task,
                main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
                summary="Terminal task was not overwritten by Main Agent error.",
                error_code=error_code,
                error_message=message,
            )

        log_with_context(
            LOGGER,
            logging.ERROR,
            "Main Agent episode failed",
            task_id=task_id,
            openai_trace_id=task.trace.openai_trace_id,
            main_agent_run_id=task.trace.latest_main_agent_run_id,
            error_code=error_code,
            error_message=message,
        )
        self.event_service.append_event(
            build_main_agent_event(
                task_id=task_id,
                event_type=EventType.MAIN_AGENT_DECISION,
                title="Main Agent error",
                message=message,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                severity=EventSeverity.ERROR,
                payload={
                    "task_id": task_id,
                    "error_code": error_code,
                    "error_message": message,
                },
                created_at=utc_now(),
            )
        )
        if terminal_status is not None:
            output = episode_output_from_task(
                task,
                main_agent_run_id=task.trace.latest_main_agent_run_id or "not-started",
                summary=message or error_code,
                final_task_status=terminal_status,
                error_code=error_code,
                error_message=message,
            )
            recorder = MainAgentObservabilityRecorder(
                session=self.session,
                artifact_root=self.artifact_root,
                task_id=task_id,
                openai_trace_id=task.trace.openai_trace_id,
                main_agent_run_id=task.trace.latest_main_agent_run_id,
                checkpoint=self.checkpoint,
            )
            final_report = recorder.write_final_report(output)
            replay_log = recorder.write_replay_log(
                final_output=output,
                error={
                    "error_code": error_code,
                    "error_message": message,
                },
            )
            recorder.record_completed(
                output=output,
                final_report=final_report,
                replay_log=replay_log,
            )
            task = self._fresh_task_for_update(task_id)
            now = utc_now()
            updated = task.model_copy(
                deep=True,
                update={
                    "status": terminal_status,
                    "phase": TaskPhase.COMPLETED.value,
                    "updated_at": now,
                    "completed_at": now,
                },
            )
            self.task_repository.update_task_state(updated)
            self.event_service.append_event(
                build_task_event(
                    task_id=task_id,
                    event_type=TERMINAL_EVENT_BY_STATUS[terminal_status],
                    title=_terminal_event_title(terminal_status),
                    message=f"The task was marked {terminal_status}.",
                    openai_trace_id=updated.trace.openai_trace_id,
                    main_agent_run_id=updated.trace.latest_main_agent_run_id,
                    payload={
                        "task_id": task_id,
                        "status": terminal_status,
                        "error_code": error_code,
                    },
                    created_at=now,
                )
            )
        self._checkpoint()
        latest = self.task_repository.get_task(task_id)
        return episode_output_from_task(
            latest,
            main_agent_run_id=latest.trace.latest_main_agent_run_id or "not-started",
            summary=message or error_code,
            error_code=error_code,
            error_message=message,
        )

    def _checkpoint(self) -> None:
        if self.checkpoint is not None:
            self.checkpoint()

    def _fresh_task(self, task_id: str) -> TaskState:
        if self.checkpoint is not None:
            self.session.expire_all()
        return self.task_repository.get_task(task_id)

    def _fresh_task_for_update(self, task_id: str) -> TaskState:
        if self.checkpoint is not None:
            self.session.expire_all()
        return self.task_repository.get_task_for_update(task_id)


def build_intake_agent(*, model: str | None = None) -> Any:
    _require_agents_sdk()
    return Agent(
        name=INTAKE_AGENT_NAME,
        instructions=build_intake_instructions(),
        model=model,
        output_type=_agent_output_schema(IntakeClassificationOutput),
    )


def build_tool_loop_agent(*, model: str | None = None) -> ToolLoopAgent:
    return ToolLoopAgent(
        name=ORCHESTRATION_AGENT_NAME,
        instructions=build_orchestration_instructions(),
        model=model,
    )


def build_orchestration_agent(*, model: str | None = None) -> Any:
    _require_agents_sdk()
    return Agent(
        name=ORCHESTRATION_AGENT_NAME,
        instructions=build_orchestration_instructions(),
        model=model,
        tools=get_main_agent_tools(),
        output_type=_agent_output_schema(MainAgentEpisodeOutput),
    )


def _agent_output_schema(output_type: type[Any]) -> Any:
    return AgentOutputSchema(output_type, strict_json_schema=False)


def build_run_config(task: TaskState, *, phase: str) -> Any:
    _require_agents_sdk()
    return RunConfig(
        workflow_name="Router Main Agent",
        trace_id=task.trace.openai_trace_id,
        group_id=task.task_id,
        trace_metadata={
            "task_id": task.task_id,
            "session_id": task.session_id,
            "main_agent_run_id": task.trace.latest_main_agent_run_id or "",
            "phase": phase,
        },
    )


def build_tool_loop_run_config(task: TaskState, *, phase: str) -> ToolLoopRunConfig:
    return ToolLoopRunConfig(
        workflow_name="Router Main Agent",
        trace_id=task.trace.openai_trace_id,
        group_id=task.task_id,
        trace_metadata={
            "task_id": task.task_id,
            "session_id": task.session_id,
            "main_agent_run_id": task.trace.latest_main_agent_run_id or "",
            "phase": phase,
        },
    )


def build_state_view(task: TaskState) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "session_id": task.session_id,
        "status": _value(task.status),
        "phase": _value(task.phase),
        "user_goal": task.raw_user_request,
        "normalized_goal": task.normalized_goal,
        "task_type": _value(task.task_type),
        "difficulty": {
            "level": _value(task.difficulty.level),
            "score": task.difficulty.score,
            "confidence": task.difficulty.confidence,
            "reasons": list(task.difficulty.reasons),
            "signals": task.difficulty.signals.model_dump(mode="json"),
            "requires_test": task.difficulty.requires_test,
            "requires_formal": task.difficulty.requires_formal,
            "requires_repair_loop": task.difficulty.requires_repair_loop,
            "need_clarification": task.difficulty.need_clarification,
        },
        "gates": task.gates.model_dump(mode="json"),
        "current_artifacts": _current_artifact_view(task.current_artifacts),
        "open_failures": _failure_summaries(task.failures),
        "repair_rounds": (
            f"{task.runtime_limits.repair_rounds}/"
            f"{task.runtime_limits.max_repair_rounds}"
        ),
        "runtime_limits": {
            "max_parallel_workers": task.runtime_limits.max_parallel_workers,
            "active_parallel_workers": task.runtime_limits.active_parallel_workers,
            "max_worker_calls": task.runtime_limits.max_worker_calls,
            "worker_calls_used": task.runtime_limits.worker_calls_used,
        },
        "active_worker_jobs": [
            job.model_dump(mode="json")
            for job in task.active_worker_jobs
        ],
        "completed_worker_job_ids": list(task.completed_worker_job_ids),
        "available_tools": _available_tools(task),
        "trace": task.trace.model_dump(mode="json"),
    }


def normalize_classification(
    classification: IntakeClassificationOutput,
) -> IntakeClassificationOutput:
    level = _value(classification.difficulty_level)
    reasons = list(classification.difficulty_reasons)
    requires_test = classification.requires_test
    requires_formal = classification.requires_formal
    requires_repair_loop = classification.requires_repair_loop

    if DIFFICULTY_RANK[level] >= DIFFICULTY_RANK[DifficultyLevel.L2.value]:
        requires_test = True

    if _has_safety_signal(classification):
        if DIFFICULTY_RANK[level] < DIFFICULTY_RANK[DifficultyLevel.L3.value]:
            level = DifficultyLevel.L3.value
            reasons.append(
                "Runtime elevated difficulty to L3 for safety-critical signals."
            )
        requires_test = True
        requires_formal = True

    if classification.task_type == TaskType.REPAIR_EXISTING_CODE.value:
        requires_repair_loop = True

    return classification.model_copy(
        update={
            "difficulty_level": level,
            "difficulty_reasons": reasons,
            "requires_test": requires_test,
            "requires_formal": requires_formal,
            "requires_repair_loop": requires_repair_loop,
        }
    )


def episode_output_from_task(
    task: TaskState,
    *,
    main_agent_run_id: str,
    summary: str,
    decisions: list[MainAgentDecision] | None = None,
    artifact_refs: list[MainAgentArtifactReference] | None = None,
    final_task_status: TaskStatus | str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> MainAgentEpisodeOutput:
    next_action = (
        "ask_user"
        if _value(task.status) == TaskStatus.WAITING_USER.value
        else "none"
    )
    return MainAgentEpisodeOutput(
        task_id=task.task_id,
        main_agent_run_id=main_agent_run_id,
        final_task_status=(
            final_task_status if final_task_status is not None else task.status
        ),
        phase=_value(task.phase),
        decisions=decisions or [],
        artifact_refs=artifact_refs or _all_artifact_refs(task.current_artifacts),
        gate_summary=MainAgentGateSummary.model_validate(
            task.gates.model_dump(mode="json")
        ),
        open_clarification_question_ids=[
            question.question_id
            for question in task.unresolved_questions
            if _value(question.status) == "open"
        ],
        next_recommended_action=next_action,
        summary=summary,
        error_code=error_code,
        error_message=error_message,
    )


def _assistant_message_for_history(turn: _AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant"}
    if turn.content:
        message["content"] = turn.content
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                },
            }
            for tool_call in turn.tool_calls
        ]
    return message


def _execute_tool_call(
    *,
    context: AgentToolContext,
    task_id: str,
    tool_call: _ChatToolCall,
    recorder: MainAgentObservabilityRecorder | None,
    turn_index: int | None,
) -> AgentToolResult:
    try:
        arguments = _parse_tool_arguments(tool_call.arguments)
    except ValueError as exc:
        _record_unexecuted_tool_call(
            recorder=recorder,
            tool_call=tool_call,
            arguments={"raw_arguments": tool_call.arguments},
            turn_index=turn_index,
            rationale_summary="Main Agent provided malformed tool arguments.",
        )
        result = AgentToolResult(
            tool=tool_call.name or "unknown",
            task_id=task_id,
            status=ToolStatus.FAILED,
            summary=str(exc),
            error=ToolError(
                error_code="invalid_tool_arguments",
                message=str(exc),
                retryable=True,
            ),
        )
        if recorder is not None:
            recorder.record_tool_result(
                tool_name=tool_call.name or "unknown",
                result=result,
                turn_index=turn_index,
            )
        return result

    unknown_tool = tool_call.name not in MAIN_AGENT_TOOL_NAMES
    if unknown_tool:
        _record_unexecuted_tool_call(
            recorder=recorder,
            tool_call=tool_call,
            arguments=arguments,
            turn_index=turn_index,
            rationale_summary="Main Agent selected an unknown tool.",
        )

    try:
        result = call_main_agent_tool(context, tool_call.name, arguments)
        if unknown_tool and recorder is not None:
            recorder.record_tool_result(
                tool_name=tool_call.name or "unknown",
                result=result,
                turn_index=turn_index,
            )
        return result
    except TypeError as exc:
        _record_unexecuted_tool_call(
            recorder=recorder,
            tool_call=tool_call,
            arguments=arguments,
            turn_index=turn_index,
            rationale_summary="Main Agent provided invalid tool arguments.",
        )
        result = AgentToolResult(
            tool=tool_call.name,
            task_id=task_id,
            status=ToolStatus.REJECTED,
            summary=f"Tool arguments failed validation: {exc}",
            error=ToolError(
                error_code="tool_argument_validation_error",
                message=str(exc),
                retryable=True,
            ),
        )
    except Exception as exc:
        _record_unexecuted_tool_call(
            recorder=recorder,
            tool_call=tool_call,
            arguments=arguments,
            turn_index=turn_index,
            rationale_summary="Main Agent tool execution failed before applying side effects.",
        )
        result = AgentToolResult(
            tool=tool_call.name,
            task_id=task_id,
            status=ToolStatus.FAILED,
            summary=f"Tool execution failed: {exc}",
            error=ToolError(
                error_code=type(exc).__name__,
                message=str(exc),
                retryable=False,
            ),
        )

    if recorder is not None:
        recorder.record_tool_result(
            tool_name=tool_call.name,
            result=result,
            turn_index=turn_index,
        )
    return result


def _record_unexecuted_tool_call(
    *,
    recorder: MainAgentObservabilityRecorder | None,
    tool_call: _ChatToolCall,
    arguments: dict[str, Any],
    turn_index: int | None,
    rationale_summary: str,
) -> None:
    if recorder is None:
        return
    recorder.record_tool_call(
        tool_name=tool_call.name or "unknown",
        arguments=arguments,
        rationale_summary=rationale_summary,
        turn_index=turn_index,
    )


def _parse_tool_arguments(raw_arguments: str | None) -> dict[str, Any]:
    if raw_arguments is None or raw_arguments == "":
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tool arguments are not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must decode to a JSON object")
    return parsed


def _tool_result_content(result: AgentToolResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=True)


def _episode_summary_from_tool_result(result: AgentToolResult) -> str:
    if result.summary:
        return result.summary
    return f"Main Agent stopped after {result.tool}."


def _assistant_turn_from_response(response: Any) -> _AssistantTurn:
    if _has_choices(response):
        return _assistant_turn_from_message(_first_choice_message(response))
    return _assistant_turn_from_stream(response)


def _assistant_turn_from_message(message: Any) -> _AssistantTurn:
    return _AssistantTurn(
        content=_content_to_text(_get_value(message, "content")),
        tool_calls=[
            _chat_tool_call_from_provider_call(call)
            for call in (_get_value(message, "tool_calls") or [])
        ],
    )


def _assistant_turn_from_stream(chunks: Any) -> _AssistantTurn:
    content_parts: list[str] = []
    tool_call_parts: dict[int, dict[str, str]] = {}
    for chunk in chunks:
        if not _has_choices(chunk):
            continue
        choice = _first_choice(chunk)
        delta = _get_value(choice, "delta")
        if delta is None:
            continue
        content = _content_to_text(_get_value(delta, "content"))
        if content:
            content_parts.append(content)
        for call in _get_value(delta, "tool_calls") or []:
            index = _get_value(call, "index")
            if index is None:
                index = len(tool_call_parts)
            part = tool_call_parts.setdefault(
                int(index),
                {"id": "", "name": "", "arguments": ""},
            )
            call_id = _get_value(call, "id")
            if call_id:
                part["id"] = str(call_id)
            function = _get_value(call, "function") or {}
            name = _get_value(function, "name")
            if name:
                part["name"] += str(name)
            arguments = _get_value(function, "arguments")
            if arguments:
                part["arguments"] += str(arguments)
    return _AssistantTurn(
        content="".join(content_parts).strip() or None,
        tool_calls=[
            _ChatToolCall(
                tool_call_id=part["id"] or prefixed_id("tool-call"),
                name=part["name"],
                arguments=part["arguments"],
            )
            for _, part in sorted(tool_call_parts.items())
        ],
    )


def _chat_tool_call_from_provider_call(call: Any) -> _ChatToolCall:
    function = _get_value(call, "function") or {}
    return _ChatToolCall(
        tool_call_id=str(_get_value(call, "id") or prefixed_id("tool-call")),
        name=str(_get_value(function, "name") or ""),
        arguments=str(_get_value(function, "arguments") or "{}"),
    )


def _has_choices(value: Any) -> bool:
    choices = _get_value(value, "choices")
    return isinstance(choices, list) and bool(choices)


def _first_choice_message(response: Any) -> Any:
    return _get_value(_first_choice(response), "message")


def _first_choice(response: Any) -> Any:
    choices = _get_value(response, "choices")
    if not isinstance(choices, list) or not choices:
        raise MainAgentProviderError("Main Agent provider response has no choices")
    return choices[0]


def _get_value(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _content_to_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip() or None
    return str(content).strip() or None


def build_main_agent_event(
    *,
    task_id: str,
    event_type: EventType,
    title: str,
    message: str | None,
    openai_trace_id: str | None,
    main_agent_run_id: str | None,
    payload: dict[str, Any],
    created_at: Any,
    severity: EventSeverity = EventSeverity.INFO,
    artifact_ids: list[str] | None = None,
    failure_ids: list[str] | None = None,
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task_id,
        seq=0,
        type=event_type,
        source=EventSource(
            type=EventSourceType.MAIN_AGENT,
            id=main_agent_run_id,
        ),
        severity=severity,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=openai_trace_id,
            main_agent_run_id=main_agent_run_id,
            artifact_ids=artifact_ids,
            failure_ids=failure_ids,
        ),
        payload=_json_payload(payload),
        created_at=created_at,
    )


def build_task_event(
    *,
    task_id: str,
    event_type: EventType,
    title: str,
    message: str | None,
    openai_trace_id: str | None,
    main_agent_run_id: str | None,
    payload: dict[str, Any],
    created_at: Any,
) -> RouterEvent:
    return RouterEvent(
        schema_version="router.v1",
        event_id=new_event_id(),
        task_id=task_id,
        seq=0,
        type=event_type,
        source=EventSource(type=EventSourceType.RUNTIME),
        severity=EventSeverity.INFO,
        visibility=EventVisibility.USER,
        title=title,
        message=message,
        correlation=EventCorrelation(
            openai_trace_id=openai_trace_id,
            main_agent_run_id=main_agent_run_id,
        ),
        payload=_json_payload(payload),
        created_at=created_at,
    )


def new_main_agent_run_id() -> str:
    return prefixed_id("main-agent-run")


def _current_artifact_view(current_artifacts: CurrentArtifacts) -> dict[str, Any]:
    view: dict[str, Any] = {
        "all_artifact_ids": list(current_artifacts.all_artifact_ids)
    }
    for field_name, value in current_artifacts:
        if field_name == "all_artifact_ids" or value is None:
            continue
        view[field_name] = _artifact_ref_view(value)
    return view


def _all_artifact_refs(current_artifacts: CurrentArtifacts) -> list[MainAgentArtifactReference]:
    refs: list[MainAgentArtifactReference] = []
    seen: set[str] = set()
    for field_name, value in current_artifacts:
        if field_name == "all_artifact_ids" or value is None:
            continue
        if value.artifact_id in seen:
            continue
        refs.append(MainAgentArtifactReference(**_artifact_ref_view(value)))
        seen.add(value.artifact_id)
    return refs


def _artifact_ref_view(artifact: ArtifactRef) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "type": _value(artifact.type),
        "version": artifact.version,
        "uri": artifact.uri,
        "summary": artifact.summary,
        "content_hash": artifact.content_hash,
    }


def _failure_summaries(failures: list[Failure]) -> list[dict[str, Any]]:
    return [
        {
            "failure_id": failure.failure_id,
            "source": _value(failure.source),
            "severity": _value(failure.severity),
            "status": _value(failure.status),
            "title": failure.title,
            "evidence_artifact_ids": list(failure.evidence_artifact_ids),
        }
        for failure in failures
        if _value(failure.status) == "open"
    ]


def _available_tools(task: TaskState) -> list[str]:
    if _is_terminal(task):
        return []
    if _value(task.status) == TaskStatus.WAITING_USER.value:
        return []
    return list(MAIN_AGENT_TOOL_NAMES)


def _default_runner(
    *,
    provider: str,
    stream: bool,
    chat_client: MainAgentChatClient | None,
) -> MainAgentRunner:
    if provider == "legacy_openai_agents":
        return OpenAIAgentsRunner()
    return OpenAICompatibleToolLoopRunner(
        chat_client=chat_client,
        stream=stream,
    )


def _uses_legacy_structured_runner(runner: MainAgentRunner) -> bool:
    return (
        isinstance(runner, OpenAIAgentsRunner)
        or (
            hasattr(runner, "run_intake")
            and not getattr(runner, "uses_tool_loop_side_effects", False)
        )
    )


def _needs_classification(task: TaskState) -> bool:
    return (
        _value(task.status) == TaskStatus.CREATED.value
        or _value(task.phase) == TaskPhase.INTAKE.value
        or _value(task.task_type) == TaskType.UNKNOWN.value
    )


def _is_terminal(task: TaskState) -> bool:
    return _value(task.status) in TERMINAL_STATUSES


def _terminal_event_title(final_status: str) -> str:
    if final_status == TaskStatus.SUCCEEDED.value:
        return "Task succeeded"
    if final_status == TaskStatus.PARTIAL_FAILED.value:
        return "Task partially failed"
    if final_status == TaskStatus.FAILED.value:
        return "Task failed"
    if final_status == TaskStatus.CANCELLED.value:
        return "Task cancelled"
    return "Task completed"


def _has_safety_signal(classification: IntakeClassificationOutput) -> bool:
    return any(
        bool(getattr(classification.difficulty_signals, field_name))
        for field_name in SAFETY_SIGNAL_FIELDS
    )


def _json_payload(payload: dict[str, Any]) -> dict[str, JsonValue]:
    return {
        str(key): _json_value(value)
        for key, value in payload.items()
        if value is not None
    }


def _json_value(value: Any) -> JsonValue:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _require_agents_sdk() -> None:
    if not AGENTS_SDK_AVAILABLE:
        raise MainAgentRunnerUnavailableError("OpenAI Agents SDK is not available")
