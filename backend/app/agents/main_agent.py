"""Main Agent service for Router task episodes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.agents.instructions import (
    ORCHESTRATION_AGENT_NAME,
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
    get_main_agent_tool_names,
)
from app.core.ids import new_event_id, prefixed_id
from app.core.logging import log_with_context
from app.core.time import utc_now
from app.mcp.mock_worker import DEFAULT_MOCK_SCENARIO
from app.models.router_schema import (
    AgentRunState,
    DEFAULT_SCHEMA_VERSION,
    DifficultyLevel,
    ExecutionPolicy,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    Failure,
    RouterEvent,
    TaskPhase,
    TaskState,
    TaskStatus,
    TaskTrace,
    TaskType,
    TokenUsage,
    WorkspaceContext,
)
from app.repositories.task_repo import TaskRepository
from app.services.event_service import EventService
from app.services.scheduler_guard import SchedulerGuardViolation


DEFAULT_MAIN_AGENT_MAX_TURNS = 20
MAX_STOP_BLOCKS = 2
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
MAIN_AGENT_TOOL_NAMES = get_main_agent_tool_names()
EVIDENCE_TOOL_NAMES = {
    "list_files",
    "glob",
    "grep",
    "read_file",
    "write_file",
    "apply_patch",
    "exec_command",
    "git_status",
    "plc_dev",
    "plc_test",
    "plc_formal",
    "plc_repair",
    "run_quality_gate",
    "record_validation_report",
}
EXECUTION_REQUEST_KEYWORDS = (
    "plc",
    "code",
    "file",
    "workspace",
    "modify",
    "write",
    "create",
    "build",
    "implement",
    "test",
    "verify",
    "debug",
    "command",
    "patch",
    "artifact",
    "代码",
    "文件",
    "修改",
    "写",
    "创建",
    "生成",
    "实现",
    "测试",
    "验证",
    "调试",
    "命令",
    "补丁",
)


class MaxTurnsExceeded(Exception):
    """Raised when a Main Agent runner exceeds its turn budget."""


class ModelBehaviorError(Exception):
    """Raised when a Main Agent runner reports invalid model behavior."""


def gen_trace_id() -> str:
    return f"trace_{uuid4().hex}"


class MainAgentServiceError(Exception):
    """Base class for Main Agent service failures."""


class MainAgentRunner(Protocol):
    """Runner boundary used by production tool-loop calls and deterministic tests."""

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
    token_usage: TokenUsage | None = None


@dataclass(frozen=True)
class _StopDecision:
    allowed: bool
    reason: str


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
        stop_block_count = 0

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
            if recorder is not None:
                recorder.add_token_usage(assistant_turn.token_usage)
            assistant_message = _assistant_message_for_history(assistant_turn)
            messages.append(assistant_message)

            if assistant_turn.tool_calls and assistant_turn.content and recorder is not None:
                recorder.record_progress_message(
                    content=assistant_turn.content,
                    turn_index=turn_index,
                )

            if not assistant_turn.tool_calls:
                if assistant_turn.content:
                    stop_decision = _evaluate_stop_request(
                        context=context,
                        task_id=task_id,
                    )
                    if stop_decision.allowed:
                        current = TaskRepository(context.session).get_task(task_id)
                        return _finalize_runtime_stop(
                            context=context,
                            task_id=task_id,
                            final_response=assistant_turn.content,
                            final_status=_final_status_for_stop(context, current),
                            source="assistant_stop",
                            turn_index=turn_index,
                        )
                    if recorder is not None:
                        recorder.record_progress_message(
                            content=assistant_turn.content,
                            turn_index=turn_index,
                        )
                    stop_block_count += 1
                    if recorder is not None:
                        recorder.record_stop_blocked(
                            reason=stop_decision.reason,
                            blocked_count=stop_block_count,
                            max_blocked_count=MAX_STOP_BLOCKS,
                            turn_index=turn_index,
                        )
                    if stop_block_count >= MAX_STOP_BLOCKS:
                        return _finalize_runtime_stop(
                            context=context,
                            task_id=task_id,
                            final_response=(
                                "I could not complete this task because required "
                                f"execution evidence is missing. {stop_decision.reason}"
                            ),
                            final_status=TaskStatus.FAILED.value,
                            source="runtime_fallback",
                            turn_index=turn_index,
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Runtime stop was blocked: "
                                f"{stop_decision.reason} Continue with the required "
                                "workspace/tool work, then provide a final answer "
                                "with no tool calls."
                            ),
                        }
                    )
                    continue

                stop_block_count += 1
                reason = "The assistant returned no content and no tool calls."
                if recorder is not None:
                    recorder.record_stop_blocked(
                        reason=reason,
                        blocked_count=stop_block_count,
                        max_blocked_count=MAX_STOP_BLOCKS,
                        turn_index=turn_index,
                    )
                if stop_block_count >= MAX_STOP_BLOCKS:
                    return _finalize_runtime_stop(
                        context=context,
                        task_id=task_id,
                        final_response=(
                            "I could not complete this task because the model "
                            "stopped without a final response or tool action."
                        ),
                        final_status=TaskStatus.FAILED.value,
                        source="runtime_fallback",
                        turn_index=turn_index,
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Runtime stop was blocked: no final response or tool "
                            "call was provided. Continue with required work or "
                            "provide a final answer with no tool calls."
                        ),
                    }
                )
                continue

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
        workspace_root: Path | None = None,
        execution_mode: str = "disabled",
        command_timeout_seconds: int = 120,
        tool_output_max_chars: int = 12_000,
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
        self.workspace_root = workspace_root
        self.execution_mode = execution_mode
        self.command_timeout_seconds = command_timeout_seconds
        self.tool_output_max_chars = tool_output_max_chars
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
            workspace_root=_workspace_root_for_task(started, self.workspace_root),
            execution_mode=_effective_execution_mode(started, self.execution_mode),
            command_timeout_seconds=self.command_timeout_seconds,
            tool_output_max_chars=self.tool_output_max_chars,
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
            state_view = build_state_view(current)
            session_context = build_session_context_view(self.session, current)
            if session_context:
                state_view["session_context"] = session_context
            output = self.runner.run_orchestration(
                agent=build_tool_loop_agent(model=self.model),
                input_text=build_state_view_prompt(state_view),
                context=context,
                max_turns=self.max_turns,
                run_config=build_tool_loop_run_config(
                    current,
                    phase="orchestration",
                ),
            )
            persisted_output = (
                output
                if getattr(self.runner, "uses_tool_loop_side_effects", False)
                else self._persist_orchestration_output(output, recorder)
            )
            self._complete_latest_agent_run(
                task_id,
                _agent_run_status_from_final_status(
                    _value(persisted_output.final_task_status)
                ),
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
        workspace_root = _workspace_root_for_task(task, self.workspace_root)
        execution_mode = _effective_execution_mode(task, self.execution_mode)
        workspace = (
            WorkspaceContext(
                root=str(workspace_root),
                current_directory=str(workspace_root),
                writable=execution_mode == "local_full_access",
            )
            if workspace_root is not None
            else task.workspace
        )
        execution_policy = ExecutionPolicy(
            mode=execution_mode,
            command_timeout_seconds=self.command_timeout_seconds,
            tool_output_max_chars=self.tool_output_max_chars,
        )
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
                "workspace": workspace,
                "execution_policy": execution_policy,
                "agent_runs": [
                    *task.agent_runs,
                    AgentRunState(
                        agent_run_id=main_agent_run_id,
                        status="running",
                        workspace=workspace,
                        execution_policy=execution_policy,
                        tool_calls=[],
                        started_at=now,
                    ),
                ],
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
                    "workspace": (
                        workspace.model_dump(mode="json")
                        if workspace is not None
                        else None
                    ),
                    "execution_policy": execution_policy.model_dump(mode="json"),
                },
                created_at=now,
            )
        )
        self._checkpoint()
        return self._fresh_task(task_id)

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

    def _complete_latest_agent_run(
        self,
        task_id: str,
        status: str,
    ) -> TaskState:
        task = self._fresh_task_for_update(task_id)
        latest_run_id = task.trace.latest_main_agent_run_id
        if latest_run_id is None:
            return task

        now = utc_now()
        updated_runs = [
            run.model_copy(update={"status": status, "completed_at": now})
            if run.agent_run_id == latest_run_id
            else run
            for run in task.agent_runs
        ]
        updated = task.model_copy(
            deep=True,
            update={
                "agent_runs": updated_runs,
                "updated_at": now,
            },
        )
        self.task_repository.update_task_state(updated)
        self._checkpoint()
        return self._fresh_task(task_id)

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
            self._complete_latest_agent_run(
                task_id,
                _agent_run_status_from_final_status(_value(task.status)),
            )
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
            self._complete_latest_agent_run(
                task_id,
                _agent_run_status_from_final_status(terminal_status),
            )
        else:
            self._complete_latest_agent_run(task_id, "failed")
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


def build_tool_loop_agent(*, model: str | None = None) -> ToolLoopAgent:
    return ToolLoopAgent(
        name=ORCHESTRATION_AGENT_NAME,
        instructions=build_orchestration_instructions(),
        model=model,
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
        "current_files": _current_file_view(task),
        "workspace": (
            task.workspace.model_dump(mode="json")
            if task.workspace is not None
            else None
        ),
        "execution_policy": (
            task.execution_policy.model_dump(mode="json")
            if task.execution_policy is not None
            else None
        ),
        "agent_runs": [run.model_dump(mode="json") for run in task.agent_runs],
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


def build_session_context_view(
    session: Session,
    current_task: TaskState,
    *,
    max_runs: int = 6,
) -> dict[str, Any] | None:
    previous_tasks = [
        task
        for task in TaskRepository(session).list_tasks_by_session(current_task.session_id)
        if task.task_id != current_task.task_id
    ][-max_runs:]
    if not previous_tasks:
        return None

    event_service = EventService(session)
    runs: list[dict[str, Any]] = []
    for task in previous_tasks:
        events = event_service.list_visible_events(task.task_id)
        final_response = next(
            (
                str(event.payload.get("content") or event.message or "")
                for event in reversed(events)
                if event.type == EventType.MAIN_AGENT_FINAL_RESPONSE
            ),
            None,
        )
        completed = next(
            (
                event
                for event in reversed(events)
                if event.type == EventType.MAIN_AGENT_COMPLETED
            ),
            None,
        )
        runs.append(
            {
                "run_id": task.task_id,
                "task_id": task.task_id,
                "status": _value(task.status),
                "user_message": task.raw_user_request,
                "final_response": _bounded_session_text(final_response),
                "final_report_path": (
                    completed.payload.get("final_report_path")
                    if completed is not None
                    else None
                ),
                "workspace_files": _workspace_file_refs_for_task(task),
                "updated_at": task.updated_at.isoformat(),
            }
        )

    return {
        "session_id": current_task.session_id,
        "current_run_id": current_task.task_id,
        "recent_runs": runs,
    }


def _bounded_session_text(value: str | None, *, limit: int = 2000) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _workspace_file_refs_for_task(
    task: TaskState,
    *,
    max_files: int = 8,
) -> list[dict[str, Any]]:
    paths = list(dict.fromkeys(task.current_files.all_paths))[-max_files:]
    return [
        {
            "path": path,
            "role": _current_file_role(task, path),
        }
        for path in paths
    ]


def _workspace_root_for_task(task: TaskState, fallback: Path | None) -> Path | None:
    if task.workspace is not None:
        return Path(task.workspace.root)
    if task.project_context.workspace_root:
        return Path(task.project_context.workspace_root)
    return fallback


def _effective_execution_mode(task: TaskState, configured: str) -> str:
    if configured != "disabled":
        return configured
    if task.workspace is None:
        return configured
    return "local_full_access" if task.workspace.writable else "local_read_only"


def _agent_run_status_from_final_status(status: str) -> str:
    if status in {
        TaskStatus.SUCCEEDED.value,
        TaskStatus.PARTIAL_FAILED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }:
        return status
    if status == TaskStatus.WAITING_USER.value:
        return status
    return TaskStatus.FAILED.value


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
        artifact_refs=artifact_refs or [],
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


def _evaluate_stop_request(
    *,
    context: AgentToolContext,
    task_id: str,
) -> _StopDecision:
    task = TaskRepository(context.session).get_task(task_id)
    if _is_terminal(task):
        return _StopDecision(allowed=True, reason="Task is already terminal.")
    return _StopDecision(allowed=True, reason="Assistant provided a final response.")


def _task_requires_tool_evidence(task: TaskState) -> bool:
    task_type = _value(task.task_type)
    if task_type not in {TaskType.UNKNOWN.value, TaskType.QA.value}:
        return True
    if task_type == TaskType.QA.value:
        return False
    if (
        task.difficulty.requires_test
        or task.difficulty.requires_formal
        or task.difficulty.requires_repair_loop
    ):
        return True
    if _value(task.difficulty.level) in {
        DifficultyLevel.L2.value,
        DifficultyLevel.L3.value,
        DifficultyLevel.L4.value,
    }:
        return True
    request = f"{task.raw_user_request}\n{task.normalized_goal or ''}".lower()
    return any(keyword in request for keyword in EXECUTION_REQUEST_KEYWORDS)


def _task_requires_quality_gate(task: TaskState) -> bool:
    _ = task
    return False


def _has_tool_evidence(context: AgentToolContext, task: TaskState) -> bool:
    return _latest_run_has_tool_evidence(task) or _events_have_tool_evidence(
        context,
        task.task_id,
    )


def _latest_run_has_tool_evidence(task: TaskState) -> bool:
    latest_run_id = task.trace.latest_main_agent_run_id
    latest_run = next(
        (
            run
            for run in reversed(task.agent_runs)
            if run.agent_run_id == latest_run_id
        ),
        None,
    )
    if latest_run is None:
        return False
    return any(
        call.tool_name in EVIDENCE_TOOL_NAMES and _value(call.status) != "rejected"
        for call in latest_run.tool_calls
    )


def _events_have_tool_evidence(context: AgentToolContext, task_id: str) -> bool:
    events = EventService(context.session).list_visible_events(task_id)
    return any(
        event.type == EventType.MAIN_AGENT_TOOL_RESULT
        and str(event.payload.get("tool_name") or "") in EVIDENCE_TOOL_NAMES
        and str(event.payload.get("status") or "") != "rejected"
        for event in events
    )


def _has_quality_gate_result(context: AgentToolContext, task: TaskState) -> bool:
    if task.current_files.latest_gate_report is not None:
        return True
    latest_run_id = task.trace.latest_main_agent_run_id
    latest_run = next(
        (
            run
            for run in reversed(task.agent_runs)
            if run.agent_run_id == latest_run_id
        ),
        None,
    )
    if latest_run is not None and any(
        call.tool_name == "run_quality_gate" and _value(call.status) != "rejected"
        for call in latest_run.tool_calls
    ):
        return True
    return any(
        event.type == EventType.MAIN_AGENT_TOOL_RESULT
        and str(event.payload.get("tool_name") or "") == "run_quality_gate"
        and str(event.payload.get("status") or "") != "rejected"
        for event in EventService(context.session).list_visible_events(task.task_id)
    )


def _has_open_blocking_failure(task: TaskState) -> bool:
    return any(
        _value(failure.status) == "open"
        and _value(failure.severity) == "blocking"
        for failure in task.failures
    )


def _final_status_for_stop(context: AgentToolContext, task: TaskState) -> str:
    if _has_open_blocking_failure(task):
        return TaskStatus.FAILED.value
    latest_run_id = task.trace.latest_main_agent_run_id
    latest_run = next(
        (
            run
            for run in reversed(task.agent_runs)
            if run.agent_run_id == latest_run_id
        ),
        None,
    )
    if latest_run is not None:
        for call in reversed(latest_run.tool_calls):
            if call.tool_name in EVIDENCE_TOOL_NAMES and _value(call.status) != "rejected":
                return (
                    TaskStatus.FAILED.value
                    if _value(call.status) == "failed"
                    else TaskStatus.SUCCEEDED.value
                )
    for event in reversed(EventService(context.session).list_visible_events(task.task_id)):
        if (
            event.type == EventType.MAIN_AGENT_TOOL_RESULT
            and str(event.payload.get("tool_name") or "") in EVIDENCE_TOOL_NAMES
            and str(event.payload.get("status") or "") != "rejected"
        ):
            return (
                TaskStatus.FAILED.value
                if str(event.payload.get("status") or "") == "failed"
                else TaskStatus.SUCCEEDED.value
            )
    return TaskStatus.SUCCEEDED.value


def _finalize_runtime_stop(
    *,
    context: AgentToolContext,
    task_id: str,
    final_response: str,
    final_status: str,
    source: str,
    turn_index: int | None,
) -> MainAgentEpisodeOutput:
    task_repository = TaskRepository(context.session)
    event_service = EventService(context.session)
    task = task_repository.get_task(task_id)
    main_agent_run_id = task.trace.latest_main_agent_run_id or "not-started"
    recorder = context.observability_recorder or MainAgentObservabilityRecorder(
        session=context.session,
        artifact_root=context.artifact_root,
        task_id=task_id,
        openai_trace_id=task.trace.openai_trace_id,
        main_agent_run_id=task.trace.latest_main_agent_run_id,
        checkpoint=context.checkpoint,
    )

    output = episode_output_from_task(
        task,
        main_agent_run_id=main_agent_run_id,
        summary=final_response,
        final_task_status=final_status,
    ).model_copy(update={"phase": TaskPhase.COMPLETED.value})
    recorder.record_final_response(
        content=final_response,
        final_status=final_status,
        source=source,
        turn_index=turn_index,
    )
    final_report = recorder.write_final_report(output)
    replay_log = recorder.write_replay_log(final_output=output)
    recorder.record_completed(
        output=output,
        final_report=final_report,
        replay_log=replay_log,
    )

    if final_status in TERMINAL_EVENT_BY_STATUS and not _is_terminal(task):
        now = utc_now()
        latest = task_repository.get_task_for_update(task_id)
        updated = latest.model_copy(
            deep=True,
            update={
                "status": final_status,
                "phase": TaskPhase.COMPLETED.value,
                "updated_at": now,
                "completed_at": now,
            },
        )
        task_repository.update_task_state(updated)
        event_service.append_event(
            build_task_event(
                task_id=task_id,
                event_type=TERMINAL_EVENT_BY_STATUS[final_status],
                title=_terminal_event_title(final_status),
                message=f"The task was marked {final_status}.",
                openai_trace_id=updated.trace.openai_trace_id,
                main_agent_run_id=updated.trace.latest_main_agent_run_id,
                payload={
                    "task_id": task_id,
                    "status": final_status,
                },
                created_at=now,
            )
        )
    if context.checkpoint is not None:
        context.checkpoint()
    return output


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
        turn = _assistant_turn_from_message(_first_choice_message(response))
        return replace(turn, token_usage=_token_usage_from_response(response))
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
    token_usage: TokenUsage | None = None
    for chunk in chunks:
        chunk_usage = _token_usage_from_response(chunk)
        if chunk_usage is not None:
            token_usage = chunk_usage
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
        token_usage=token_usage,
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


def _token_usage_from_response(response: Any) -> TokenUsage | None:
    usage = _get_value(response, "usage")
    if usage is None:
        return None

    input_tokens = _first_token_count(
        usage,
        "input_tokens",
        "prompt_tokens",
    )
    output_tokens = _first_token_count(
        usage,
        "output_tokens",
        "completion_tokens",
    )
    total_tokens = _first_token_count(usage, "total_tokens")
    if total_tokens is None:
        parts = [
            value
            for value in (input_tokens, output_tokens)
            if value is not None
        ]
        total_tokens = sum(parts) if parts else None

    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
    ):
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _first_token_count(usage: Any, *field_names: str) -> int | None:
    for field_name in field_names:
        count = _token_count_value(_get_value(usage, field_name))
        if count is not None:
            return count
    return None


def _token_count_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


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
        schema_version=DEFAULT_SCHEMA_VERSION,
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
        schema_version=DEFAULT_SCHEMA_VERSION,
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


def _current_file_view(task: TaskState) -> dict[str, Any]:
    current_files = task.current_files
    view: dict[str, Any] = {"all_paths": list(current_files.all_paths)}
    for field_name, value in current_files:
        if field_name == "all_paths" or value is None:
            continue
        view[field_name] = value
    return view


def _current_file_role(task: TaskState, path: str) -> str | None:
    for field_name, value in task.current_files:
        if field_name == "all_paths":
            continue
        if value == path:
            return field_name
    return None


def _failure_summaries(failures: list[Failure]) -> list[dict[str, Any]]:
    return [
        {
            "failure_id": failure.failure_id,
            "source": _value(failure.source),
            "severity": _value(failure.severity),
            "status": _value(failure.status),
            "title": failure.title,
            "evidence_paths": list(failure.evidence_paths),
        }
        for failure in failures
        if _value(failure.status) == "open"
    ]


def _available_tools(task: TaskState) -> list[str]:
    if _is_terminal(task):
        return []
    if _value(task.status) == TaskStatus.WAITING_USER.value:
        return []
    mode = (
        _value(task.execution_policy.mode)
        if task.execution_policy is not None
        else "disabled"
    )
    read_tools = {"list_files", "glob", "grep", "read_file", "git_status"}
    write_tools = {"write_file", "apply_patch", "exec_command"}
    if mode == "local_full_access":
        return list(MAIN_AGENT_TOOL_NAMES)
    if mode == "local_read_only":
        return [
            name
            for name in MAIN_AGENT_TOOL_NAMES
            if name not in write_tools
        ]
    return [
        name
        for name in MAIN_AGENT_TOOL_NAMES
        if name not in read_tools | write_tools
    ]


def _default_runner(
    *,
    provider: str,
    stream: bool,
    chat_client: MainAgentChatClient | None,
) -> MainAgentRunner:
    return OpenAICompatibleToolLoopRunner(
        chat_client=chat_client,
        stream=stream,
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
