"""Adapter boundary for invoking mock or real MCP PLC workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_event_id, prefixed_id
from app.core.logging import log_with_context
from app.core.time import utc_now
from app.mcp.client import (
    PlcMcpClient,
    PlcMcpConnectionError,
    PlcMcpInvalidResponseError,
    PlcMcpTimeoutError,
    PlcMcpToolError,
    PlcMcpToolNotFoundError,
)
from app.mcp.subagent_client import (
    SubagentConnectionError,
    SubagentExecutionError,
    SubagentInvalidResponseError,
    SubagentTimeoutError,
    SubagentWorkerClient,
)
from app.mcp.draft import (
    LlmWorkerDraftOutput,
    McpDraftValidationError,
    McpInputFileSnapshot,
    McpWorkerRequest,
    validate_worker_draft_output,
)
from app.models.router_schema import (
    ArtifactType,
    EventCorrelation,
    EventSeverity,
    EventSource,
    EventSourceType,
    EventType,
    EventVisibility,
    Failure,
    FailureSource,
    RouterEvent,
    WorkerExecutionStatus,
    WorkerInput,
    WorkerJobStatus,
    WorkerResult,
    WorkerType,
)
from app.repositories.worker_job_repo import WorkerJobRepository
from app.services.event_service import EventService
from app.mcp.mock_worker import (
    DEFAULT_MOCK_SCENARIO,
    MockArtifactWriteIntent,
    MockWorkerExecutionError,
    MockWorkerOutput,
    MockWorkerSchemaInvalid,
    MockWorkerTimeout,
    run_mock_worker,
)
from app.mcp.normalizer import (
    WorkerResultNormalizationError,
    connection_error_worker_result,
    execution_error_worker_result,
    normalize_worker_result,
    schema_invalid_worker_result,
    timeout_worker_result,
)


MockRunner = Callable[..., MockWorkerOutput]
CheckpointCallback = Callable[[], None]
RealWorkerMode = str
LOGGER = logging.getLogger(__name__)

class McpAdapterUnsupportedModeError(Exception):
    """Raised when the configured MCP mode is not implemented by this adapter."""


class McpAdapter:
    """Coordinates worker invocation, persistence, result normalization, and events."""

    def __init__(
        self,
        *,
        session: Session,
        artifact_root: Path,
        mcp_mode: str = "mock",
        mock_scenario: str = DEFAULT_MOCK_SCENARIO,
        mock_runner: MockRunner | None = None,
        mcp_client: PlcMcpClient | None = None,
        subagent_client: SubagentWorkerClient | None = None,
        plc_worker_mcp_url: str | None = None,
        plc_worker_timeout_seconds: int | None = None,
        plc_worker_artifact_max_chars: int | None = None,
        subagent_api_base_url: str | None = None,
        subagent_api_token: str | None = None,
        subagent_timeout_seconds: int | None = None,
        subagent_max_retries: int | None = None,
        subagent_retry_backoff_seconds: float | None = None,
        plc_dev_mode: str | None = None,
        plc_test_mode: str | None = None,
        plc_formal_mode: str | None = None,
        plc_repair_mode: str | None = None,
        checkpoint: CheckpointCallback | None = None,
    ) -> None:
        settings = get_settings()
        self.session = session
        self.artifact_root = artifact_root
        self.event_service = EventService(session)
        self.worker_job_repository = WorkerJobRepository(session)
        self.mcp_mode = mcp_mode
        self.mock_scenario = mock_scenario
        self.mock_runner = mock_runner or run_mock_worker
        self.mcp_client = mcp_client
        self.subagent_client = subagent_client
        self.plc_worker_mcp_url = plc_worker_mcp_url or settings.plc_worker_mcp_url
        self.plc_worker_timeout_seconds = (
            plc_worker_timeout_seconds or settings.plc_worker_timeout_seconds
        )
        self.plc_worker_artifact_max_chars = (
            plc_worker_artifact_max_chars or settings.plc_worker_artifact_max_chars
        )
        self.subagent_api_base_url = (
            subagent_api_base_url or settings.subagent_api_base_url
        )
        self.subagent_api_token = (
            subagent_api_token
            if subagent_api_token is not None
            else settings.subagent_api_token
        )
        self.subagent_timeout_seconds = (
            subagent_timeout_seconds or settings.subagent_timeout_seconds
        )
        self.subagent_max_retries = (
            subagent_max_retries
            if subagent_max_retries is not None
            else settings.subagent_max_retries
        )
        self.subagent_retry_backoff_seconds = (
            subagent_retry_backoff_seconds
            if subagent_retry_backoff_seconds is not None
            else settings.subagent_retry_backoff_seconds
        )
        self.worker_modes = {
            WorkerType.PLC_DEV.value: plc_dev_mode if plc_dev_mode is not None else settings.plc_dev_mode,
            WorkerType.PLC_TEST.value: plc_test_mode if plc_test_mode is not None else settings.plc_test_mode,
            WorkerType.PLC_FORMAL.value: (
                plc_formal_mode if plc_formal_mode is not None else settings.plc_formal_mode
            ),
            WorkerType.PLC_REPAIR.value: (
                plc_repair_mode if plc_repair_mode is not None else settings.plc_repair_mode
            ),
        }
        self.checkpoint = checkpoint

    def call_worker(
        self,
        worker_input: WorkerInput,
        *,
        scenario: str | None = None,
    ) -> WorkerResult:
        """Invoke a worker and persist the Router-side audit trail."""

        if self.mcp_mode not in {"mock", "real", "hybrid", "subagent"}:
            raise McpAdapterUnsupportedModeError(
                f"unsupported MCP_MODE for local adapter: {self.mcp_mode!r}"
            )

        active_scenario = scenario or self.mock_scenario
        route = self._route_for_worker(worker_input)
        if route in {"real", "subagent"}:
            worker_input = worker_input.model_copy(
                deep=True,
                update={
                    "trace_context": worker_input.trace_context.model_copy(
                        update={
                            "worker_job_id": worker_input.worker_job_id,
                            "mcp_request_id": worker_input.trace_context.mcp_request_id
                            or prefixed_id("mcp-request"),
                        }
                    )
                },
            )
        log_with_context(
            LOGGER,
            logging.INFO,
            "Worker dispatch started",
            task_id=worker_input.task_id,
            openai_trace_id=worker_input.trace_context.openai_trace_id,
            main_agent_run_id=worker_input.trace_context.main_agent_run_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            mcp_request_id=worker_input.trace_context.mcp_request_id,
            worker_route=route,
        )
        started_at = utc_now()
        self.worker_job_repository.create_job(worker_input, started_at=started_at)
        self.event_service.append_event(
            self._build_worker_event(
                worker_input=worker_input,
                event_type=EventType.WORKER_STARTED,
                title=f"{_value(worker_input.worker_type)} worker started",
                message=f"{route} worker invocation started.",
                created_at=started_at,
                artifact_ids=None,
                payload={
                    "worker_type": _value(worker_input.worker_type),
                    "mcp_tool": _value(worker_input.mcp_tool),
                    "worker_job_id": worker_input.worker_job_id,
                    "worker_route": route,
                    "input_paths": list(worker_input.input_paths),
                    "mock_scenario": active_scenario if route == "mock" else None,
                },
            )
        )
        self._checkpoint()

        try:
            if route == "mock":
                mock_output = self.mock_runner(worker_input, scenario=active_scenario)
                if not isinstance(mock_output, MockWorkerOutput):
                    raise MockWorkerSchemaInvalid("mock output is not a MockWorkerOutput")
                draft_output = _draft_from_mock_output(mock_output)
            elif route == "real":
                draft_output = self._call_real_worker(worker_input)
                validate_worker_draft_output(draft_output, worker_input)
            elif route == "subagent":
                draft_output = self._call_subagent_worker(worker_input)
                validate_worker_draft_output(draft_output, worker_input)
            else:
                raise McpAdapterUnsupportedModeError(
                    f"unsupported worker route: {route!r}"
                )

            written_paths = self._persist_file_writes(
                worker_input,
                draft_output.artifact_writes,
            )
            completed_at = utc_now()
            raw_result = self._build_success_result(
                worker_input=worker_input,
                draft_output=draft_output,
                written_paths=written_paths,
                started_at=started_at,
                completed_at=completed_at,
            )
            result = normalize_worker_result(raw_result, worker_input)
            job_status = _job_status_for_result(result)
            self.worker_job_repository.complete_job(
                worker_input.worker_job_id,
                result,
                status=job_status,
            )
            self.event_service.append_event(
                self._terminal_worker_event(
                    worker_input=worker_input,
                    result=result,
                    created_at=completed_at,
                )
            )
            self._checkpoint()
            return result

        except MockWorkerTimeout as exc:
            completed_at = utc_now()
            result = timeout_worker_result(
                worker_input,
                started_at=started_at,
                completed_at=completed_at,
                message=str(exc),
            )
            self.worker_job_repository.complete_job(
                worker_input.worker_job_id,
                result,
                status=WorkerJobStatus.TIMEOUT,
            )
            self.event_service.append_event(
                self._terminal_worker_event(
                    worker_input=worker_input,
                    result=result,
                    created_at=completed_at,
                )
            )
            self._checkpoint()
            return result

        except (PlcMcpTimeoutError, SubagentTimeoutError) as exc:
            completed_at = utc_now()
            result = timeout_worker_result(
                worker_input,
                started_at=started_at,
                completed_at=completed_at,
                message=str(exc),
            )
            self.worker_job_repository.complete_job(
                worker_input.worker_job_id,
                result,
                status=WorkerJobStatus.TIMEOUT,
            )
            self.event_service.append_event(
                self._terminal_worker_event(
                    worker_input=worker_input,
                    result=result,
                    created_at=completed_at,
                )
            )
            self._checkpoint()
            return result

        except (PlcMcpConnectionError, SubagentConnectionError) as exc:
            completed_at = utc_now()
            result = connection_error_worker_result(
                worker_input,
                started_at=started_at,
                completed_at=completed_at,
                message=str(exc),
                details=exc.details,
            )
            self.worker_job_repository.complete_job(
                worker_input.worker_job_id,
                result,
                status=WorkerJobStatus.ERROR,
            )
            self.event_service.append_event(
                self._terminal_worker_event(
                    worker_input=worker_input,
                    result=result,
                    created_at=completed_at,
                )
            )
            self._checkpoint()
            return result

        except (
            MockWorkerSchemaInvalid,
            WorkerResultNormalizationError,
            PlcMcpInvalidResponseError,
            SubagentInvalidResponseError,
            McpDraftValidationError,
        ) as exc:
            completed_at = utc_now()
            details = getattr(exc, "details", None)
            result = schema_invalid_worker_result(
                worker_input,
                started_at=started_at,
                completed_at=completed_at,
                message=str(exc),
                details=details,
            )
            self.worker_job_repository.complete_job(
                worker_input.worker_job_id,
                result,
                status=WorkerJobStatus.ERROR,
            )
            self.event_service.append_event(
                self._terminal_worker_event(
                    worker_input=worker_input,
                    result=result,
                    created_at=completed_at,
                )
            )
            self._checkpoint()
            return result

        except MockWorkerExecutionError as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

        except (PlcMcpToolNotFoundError, PlcMcpToolError, SubagentExecutionError) as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

        except Exception as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

    def _call_real_worker(self, worker_input: WorkerInput) -> LlmWorkerDraftOutput:
        client = self.mcp_client or PlcMcpClient(
            url=self.plc_worker_mcp_url,
            timeout_seconds=self.plc_worker_timeout_seconds,
        )
        log_with_context(
            LOGGER,
            logging.INFO,
            "MCP worker request sent",
            task_id=worker_input.task_id,
            openai_trace_id=worker_input.trace_context.openai_trace_id,
            main_agent_run_id=worker_input.trace_context.main_agent_run_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            mcp_request_id=worker_input.trace_context.mcp_request_id,
        )
        return client.call_worker_tool(
            _value(worker_input.mcp_tool),
            McpWorkerRequest(
                worker_input=worker_input,
                input_files=self._input_file_snapshots(worker_input),
            ),
        )

    def _call_subagent_worker(self, worker_input: WorkerInput) -> LlmWorkerDraftOutput:
        client = self.subagent_client or SubagentWorkerClient(
            base_url=self.subagent_api_base_url,
            timeout_seconds=self.subagent_timeout_seconds,
            api_token=self.subagent_api_token,
            artifact_max_chars=self.plc_worker_artifact_max_chars,
            max_retries=self.subagent_max_retries,
            retry_backoff_seconds=self.subagent_retry_backoff_seconds,
        )
        log_with_context(
            LOGGER,
            logging.INFO,
            "Subagent worker request sent",
            task_id=worker_input.task_id,
            openai_trace_id=worker_input.trace_context.openai_trace_id,
            main_agent_run_id=worker_input.trace_context.main_agent_run_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            mcp_request_id=worker_input.trace_context.mcp_request_id,
        )
        return client.call_worker(
            worker_input,
            self._input_file_snapshots(worker_input),
        )

    def _persist_file_writes(
        self,
        worker_input: WorkerInput,
        artifact_writes: Any,
    ) -> list[str]:
        written_paths: list[str] = []
        for intent in artifact_writes:
            path = _file_path_for_write(worker_input, intent)
            target = _workspace_path(worker_input, path, allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_content_bytes(intent.content))
            written_paths.append(path)
            self.event_service.append_event(
                self._build_worker_event(
                    worker_input=worker_input,
                    event_type=EventType.WORKER_PROGRESS,
                    title=f"{_value(intent.artifact_type)} file written",
                    message=intent.summary,
                    created_at=utc_now(),
                    artifact_ids=None,
                    payload={
                        "worker_type": _value(worker_input.worker_type),
                        "worker_job_id": worker_input.worker_job_id,
                        "path": path,
                        "file_type": _value(intent.artifact_type),
                        "version": intent.version,
                    },
                )
            )
            self._checkpoint()
        return written_paths

    def _build_success_result(
        self,
        *,
        worker_input: WorkerInput,
        draft_output: LlmWorkerDraftOutput,
        written_paths: list[str],
        started_at: datetime,
        completed_at: datetime,
    ) -> WorkerResult:
        report_paths = _report_paths(written_paths)
        return WorkerResult(
            schema_version="router.v2",
            task_id=worker_input.task_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            execution_status=WorkerExecutionStatus.COMPLETED,
            outcome=draft_output.outcome,
            summary=draft_output.summary,
            read_paths=list(worker_input.input_paths),
            written_paths=written_paths,
            report_paths=report_paths,
            diagnostics=list(draft_output.diagnostics),
            assumptions=list(draft_output.assumptions),
            failures=_failures_with_evidence(draft_output.failures, report_paths),
            clarification_request=draft_output.clarification_request,
            metrics=draft_output.metrics,
            next_recommended_action=draft_output.next_recommended_action,
            error=None,
            trace_context=worker_input.trace_context.model_copy(
                update={"worker_job_id": worker_input.worker_job_id}
            ),
            started_at=started_at,
            completed_at=completed_at,
            metadata=draft_output.metadata,
        )

    def _input_file_snapshots(
        self,
        worker_input: WorkerInput,
    ) -> list[McpInputFileSnapshot]:
        snapshots: list[McpInputFileSnapshot] = []
        for path in worker_input.input_paths:
            content: str | None = None
            content_truncated = False
            content_chars: int | None = None
            mime_type: str | None = _mime_type_for_path(path)
            try:
                decoded = _workspace_path(worker_input, path).read_text(encoding="utf-8")
                content_truncated = len(decoded) > self.plc_worker_artifact_max_chars
                content = decoded[: self.plc_worker_artifact_max_chars]
                content_chars = len(content)
            except Exception:
                content = None
            snapshots.append(
                McpInputFileSnapshot(
                    path=path,
                    type=_artifact_type_for_path(path),
                    version=1,
                    summary=f"Workspace file {path}",
                    uri=f"workspace://{path}",
                    content=content,
                    content_truncated=content_truncated,
                    content_chars=content_chars,
                    mime_type=mime_type,
                )
            )
        return snapshots

    def _complete_execution_error(
        self,
        worker_input: WorkerInput,
        started_at: datetime,
        exc: Exception,
    ) -> WorkerResult:
        completed_at = utc_now()
        result = execution_error_worker_result(
            worker_input,
            started_at=started_at,
            completed_at=completed_at,
            message=str(exc),
            details={"exception_type": type(exc).__name__},
        )
        self.worker_job_repository.complete_job(
            worker_input.worker_job_id,
            result,
            status=WorkerJobStatus.ERROR,
        )
        self.event_service.append_event(
            self._terminal_worker_event(
                worker_input=worker_input,
                result=result,
                created_at=completed_at,
            )
        )
        self._checkpoint()
        return result

    def _terminal_worker_event(
        self,
        *,
        worker_input: WorkerInput,
        result: WorkerResult,
        created_at: datetime,
    ) -> RouterEvent:
        execution_status = _value(result.execution_status)
        event_type = {
            WorkerExecutionStatus.COMPLETED.value: EventType.WORKER_COMPLETED,
            WorkerExecutionStatus.PARTIAL.value: EventType.WORKER_PARTIAL,
            WorkerExecutionStatus.TIMEOUT.value: EventType.WORKER_TIMEOUT,
            WorkerExecutionStatus.CANCELLED.value: EventType.WORKER_CANCELLED,
            WorkerExecutionStatus.ERROR.value: EventType.WORKER_ERROR,
        }[execution_status]
        severity = (
            EventSeverity.ERROR
            if execution_status
            in {
                WorkerExecutionStatus.ERROR.value,
                WorkerExecutionStatus.TIMEOUT.value,
            }
            else EventSeverity.INFO
        )
        failure_ids = [failure.failure_id for failure in result.failures]
        log_with_context(
            LOGGER,
            logging.INFO if severity == EventSeverity.INFO else logging.ERROR,
            "Worker dispatch completed",
            task_id=worker_input.task_id,
            openai_trace_id=worker_input.trace_context.openai_trace_id,
            main_agent_run_id=worker_input.trace_context.main_agent_run_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            mcp_request_id=worker_input.trace_context.mcp_request_id,
            execution_status=execution_status,
            outcome_status=result.outcome.status,
            error_code=result.error.error_code if result.error else None,
        )
        return self._build_worker_event(
            worker_input=worker_input,
            event_type=event_type,
            title=f"{_value(worker_input.worker_type)} worker {execution_status}",
            message=result.summary,
            created_at=created_at,
            artifact_ids=None,
            failure_ids=failure_ids or None,
            severity=severity,
            payload={
                "worker_type": _value(worker_input.worker_type),
                "mcp_tool": _value(worker_input.mcp_tool),
                "worker_job_id": worker_input.worker_job_id,
                "execution_status": execution_status,
                "outcome_status": _value(result.outcome.status),
                "read_paths": list(result.read_paths),
                "written_paths": list(result.written_paths),
                "report_paths": list(result.report_paths),
                "failure_ids": failure_ids,
                "error_code": result.error.error_code if result.error else None,
            },
        )

    def _build_worker_event(
        self,
        *,
        worker_input: WorkerInput,
        event_type: EventType,
        title: str,
        message: str | None,
        created_at: datetime,
        artifact_ids: list[str] | tuple[str, ...] | None,
        payload: dict[str, Any],
        failure_ids: list[str] | None = None,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> RouterEvent:
        return RouterEvent(
            schema_version="router.v2",
            event_id=new_event_id(),
            task_id=worker_input.task_id,
            seq=0,
            type=event_type,
            source=EventSource(
                type=EventSourceType.WORKER,
                worker_type=worker_input.worker_type,
                id=worker_input.worker_job_id,
            ),
            severity=severity,
            visibility=EventVisibility.USER,
            title=title,
            message=message,
            correlation=EventCorrelation(
                openai_trace_id=worker_input.trace_context.openai_trace_id,
                main_agent_run_id=worker_input.trace_context.main_agent_run_id,
                worker_job_id=worker_input.worker_job_id,
                mcp_request_id=worker_input.trace_context.mcp_request_id,
                artifact_ids=list(artifact_ids) if artifact_ids else None,
                failure_ids=failure_ids,
            ),
            payload=_json_payload(payload),
            created_at=created_at,
        )

    def _checkpoint(self) -> None:
        if self.checkpoint is not None:
            self.checkpoint()

    def _route_for_worker(self, worker_input: WorkerInput) -> RealWorkerMode:
        worker_type = _value(worker_input.worker_type)
        override = self.worker_modes.get(worker_type)
        if override is not None:
            return override
        if self.mcp_mode in {"real", "subagent"}:
            return self.mcp_mode
        return "mock"


def call_mcp_adapter(
    worker_input: WorkerInput,
    *,
    session: Session,
    artifact_root: Path,
    mcp_mode: str = "mock",
    mock_scenario: str = DEFAULT_MOCK_SCENARIO,
) -> WorkerResult:
    """Convenience entrypoint for scripts and future agent tools."""

    return McpAdapter(
        session=session,
        artifact_root=artifact_root,
        mcp_mode=mcp_mode,
        mock_scenario=mock_scenario,
    ).call_worker(worker_input)


def _draft_from_mock_output(mock_output: MockWorkerOutput) -> LlmWorkerDraftOutput:
    return LlmWorkerDraftOutput(
        outcome=mock_output.outcome,
        summary=mock_output.summary,
        artifact_writes=[
            {
                "artifact_type": intent.artifact_type,
                "version": intent.version,
                "name": intent.name,
                "content": intent.content,
                "summary": intent.summary,
                "visibility": intent.visibility,
                "metadata": intent.metadata,
                "parent_artifact_ids": list(intent.parent_artifact_ids),
                "mime_type": intent.mime_type,
            }
            for intent in mock_output.artifact_writes
        ],
        diagnostics=list(mock_output.diagnostics),
        failures=list(mock_output.failures),
        clarification_request=mock_output.clarification_request,
        metrics=mock_output.metrics,
        next_recommended_action=mock_output.next_recommended_action,
        metadata=mock_output.metadata,
    )


def _failures_with_evidence(
    failures: tuple[Failure, ...],
    report_paths: list[str],
) -> list[Failure]:
    evidence_paths = [path for path in report_paths if _is_evidence_path(path)]
    counterexample_path = next(
        (path for path in report_paths if "counterexample" in path.lower()),
        None,
    )
    updated_failures: list[Failure] = []
    for failure in failures:
        update: dict[str, Any] = {}
        if not failure.evidence_paths and evidence_paths:
            update["evidence_paths"] = evidence_paths
        if (
            _value(failure.source) == FailureSource.FORMAL.value
            and counterexample_path is not None
            and failure.reproduction is not None
            and failure.reproduction.counterexample_path is None
        ):
            update["reproduction"] = failure.reproduction.model_copy(
                update={"counterexample_path": counterexample_path}
            )
        updated_failures.append(failure.model_copy(update=update) if update else failure)
    return updated_failures


def _job_status_for_result(result: WorkerResult) -> WorkerJobStatus:
    execution_status = _value(result.execution_status)
    return {
        WorkerExecutionStatus.COMPLETED.value: WorkerJobStatus.COMPLETED,
        WorkerExecutionStatus.PARTIAL.value: WorkerJobStatus.PARTIAL,
        WorkerExecutionStatus.TIMEOUT.value: WorkerJobStatus.TIMEOUT,
        WorkerExecutionStatus.CANCELLED.value: WorkerJobStatus.CANCELLED,
        WorkerExecutionStatus.ERROR.value: WorkerJobStatus.ERROR,
    }[execution_status]

def _file_path_for_write(worker_input: WorkerInput, intent: Any) -> str:
    artifact_type = _value(intent.artifact_type)
    for path in worker_input.output_paths:
        if _path_matches_artifact_type(path, artifact_type):
            return path
    return f".router/reports/{worker_input.worker_job_id}/{Path(intent.name).name}"


def _workspace_path(
    worker_input: WorkerInput,
    path: str,
    *,
    allow_missing: bool = False,
) -> Path:
    root = Path(worker_input.workspace_root).resolve()
    target = (root / path).resolve(strict=False)
    target.relative_to(root)
    if not allow_missing and not target.exists():
        raise FileNotFoundError(path)
    return target


def _content_bytes(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    import json

    return json.dumps(content, ensure_ascii=True, indent=2).encode("utf-8")


def _report_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if path.startswith(".router/") or _is_evidence_path(path)]


def _is_evidence_path(path: str) -> bool:
    lower = path.lower()
    return any(
        token in lower
        for token in (
            "test_report",
            "failing_trace",
            "formal_report",
            "counterexample",
            "repair_summary",
            "patch",
            "gate_report",
        )
    )


def _path_matches_artifact_type(path: str, artifact_type: str) -> bool:
    lower = path.lower()
    if artifact_type == ArtifactType.PLC_CODE.value:
        return lower.endswith((".st", ".scl", ".fbd", ".xml")) and "io_contract" not in lower
    if artifact_type == ArtifactType.REQUIREMENTS_IR.value:
        return "requirements" in lower
    if artifact_type == ArtifactType.IO_CONTRACT.value:
        return "io_contract" in lower
    if artifact_type == ArtifactType.TEST_REPORT.value:
        return "test_report" in lower
    if artifact_type == ArtifactType.FAILING_TRACE.value:
        return "failing_trace" in lower
    if artifact_type == ArtifactType.FORMAL_REPORT.value:
        return "formal_report" in lower
    if artifact_type == ArtifactType.COUNTEREXAMPLE.value:
        return "counterexample" in lower
    if artifact_type == ArtifactType.PATCH.value:
        return lower.endswith((".diff", ".patch")) or "patch" in lower
    if artifact_type == ArtifactType.REPAIR_SUMMARY.value:
        return "repair_summary" in lower
    return False


def _artifact_type_for_path(path: str) -> ArtifactType:
    lower = path.lower()
    if lower.endswith((".st", ".scl", ".fbd", ".xml")) and "io_contract" not in lower:
        return ArtifactType.PLC_CODE
    if "requirements" in lower:
        return ArtifactType.REQUIREMENTS_IR
    if "test_report" in lower:
        return ArtifactType.TEST_REPORT
    if "failing_trace" in lower:
        return ArtifactType.FAILING_TRACE
    if "formal_report" in lower:
        return ArtifactType.FORMAL_REPORT
    if "counterexample" in lower:
        return ArtifactType.COUNTEREXAMPLE
    if lower.endswith((".diff", ".patch")) or "patch" in lower:
        return ArtifactType.PATCH
    return ArtifactType.MISC


def _mime_type_for_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith((".diff", ".patch")):
        return "text/x-diff"
    if lower.endswith(".md"):
        return "text/markdown"
    return "text/plain"


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json_value(value)
        for key, value in payload.items()
        if value is not None
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
