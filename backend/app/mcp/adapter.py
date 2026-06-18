"""Adapter boundary for invoking mock or real MCP PLC workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_artifact_id, new_event_id, prefixed_id
from app.core.time import utc_now
from app.mcp.client import (
    PlcMcpClient,
    PlcMcpConnectionError,
    PlcMcpInvalidResponseError,
    PlcMcpTimeoutError,
    PlcMcpToolError,
    PlcMcpToolNotFoundError,
)
from app.mcp.draft import (
    LlmWorkerDraftOutput,
    McpDraftValidationError,
    McpInputArtifactSnapshot,
    McpWorkerRequest,
    validate_worker_draft_output,
)
from app.models.router_schema import (
    ArtifactCreator,
    ArtifactCreatorType,
    ArtifactRef,
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
from app.services.artifact_store import ArtifactContentWrite, ArtifactStore
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

EVIDENCE_ARTIFACT_TYPES = {
    ArtifactType.TEST_REPORT.value,
    ArtifactType.FAILING_TRACE.value,
    ArtifactType.FORMAL_REPORT.value,
    ArtifactType.COUNTEREXAMPLE.value,
}


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
        plc_worker_mcp_url: str | None = None,
        plc_worker_timeout_seconds: int | None = None,
        plc_worker_artifact_max_chars: int | None = None,
        plc_dev_mode: str | None = None,
        plc_test_mode: str | None = None,
        plc_formal_mode: str | None = None,
        plc_repair_mode: str | None = None,
        checkpoint: CheckpointCallback | None = None,
    ) -> None:
        settings = get_settings()
        self.session = session
        self.artifact_store = ArtifactStore(
            session=session,
            artifact_root=artifact_root,
        )
        self.event_service = EventService(session)
        self.worker_job_repository = WorkerJobRepository(session)
        self.mcp_mode = mcp_mode
        self.mock_scenario = mock_scenario
        self.mock_runner = mock_runner or run_mock_worker
        self.mcp_client = mcp_client
        self.plc_worker_mcp_url = plc_worker_mcp_url or settings.plc_worker_mcp_url
        self.plc_worker_timeout_seconds = (
            plc_worker_timeout_seconds or settings.plc_worker_timeout_seconds
        )
        self.plc_worker_artifact_max_chars = (
            plc_worker_artifact_max_chars or settings.plc_worker_artifact_max_chars
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

        if self.mcp_mode not in {"mock", "real", "hybrid"}:
            raise McpAdapterUnsupportedModeError(
                f"unsupported MCP_MODE for local adapter: {self.mcp_mode!r}"
            )

        active_scenario = scenario or self.mock_scenario
        route = self._route_for_worker(worker_input)
        if route == "real":
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
        started_at = utc_now()
        self.worker_job_repository.create_job(worker_input, started_at=started_at)
        self.event_service.append_event(
            self._build_worker_event(
                worker_input=worker_input,
                event_type=EventType.WORKER_STARTED,
                title=f"{_value(worker_input.worker_type)} worker started",
                message="Mock worker invocation started.",
                created_at=started_at,
                artifact_ids=_input_artifact_ids(worker_input),
                payload={
                    "worker_type": _value(worker_input.worker_type),
                    "mcp_tool": _value(worker_input.mcp_tool),
                    "worker_job_id": worker_input.worker_job_id,
                    "worker_route": route,
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
            else:
                draft_output = self._call_real_worker(worker_input)
                validate_worker_draft_output(draft_output, worker_input)

            produced_refs = self._persist_artifact_writes(
                worker_input,
                draft_output.artifact_writes,
            )
            completed_at = utc_now()
            raw_result = self._build_success_result(
                worker_input=worker_input,
                draft_output=draft_output,
                produced_artifacts=produced_refs,
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

        except PlcMcpTimeoutError as exc:
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

        except PlcMcpConnectionError as exc:
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

        except (PlcMcpToolNotFoundError, PlcMcpToolError) as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

        except Exception as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

    def _call_real_worker(self, worker_input: WorkerInput) -> LlmWorkerDraftOutput:
        client = self.mcp_client or PlcMcpClient(
            url=self.plc_worker_mcp_url,
            timeout_seconds=self.plc_worker_timeout_seconds,
        )
        return client.call_worker_tool(
            _value(worker_input.mcp_tool),
            McpWorkerRequest(
                worker_input=worker_input,
                input_artifacts=self._input_artifact_snapshots(worker_input),
            ),
        )

    def _persist_artifact_writes(
        self,
        worker_input: WorkerInput,
        artifact_writes: Any,
    ) -> list[ArtifactRef]:
        produced_refs: list[ArtifactRef] = []
        for intent in artifact_writes:
            artifact = self.artifact_store.write_artifact_content(
                _artifact_write_request(worker_input, intent)
            ).artifact
            artifact_ref = self.artifact_store.get_artifact_ref(artifact.artifact_id)
            produced_refs.append(artifact_ref)
            self.event_service.append_event(
                self._build_worker_event(
                    worker_input=worker_input,
                    event_type=EventType.ARTIFACT_CREATED,
                    title=f"{_value(artifact.type)} artifact created",
                    message=artifact.summary,
                    created_at=artifact.created_at,
                    artifact_ids=[artifact.artifact_id],
                    payload={
                        "worker_type": _value(worker_input.worker_type),
                        "worker_job_id": worker_input.worker_job_id,
                        "artifact_id": artifact.artifact_id,
                        "artifact_type": _value(artifact.type),
                        "version": artifact.version,
                    },
                )
            )
            self._checkpoint()
        return produced_refs

    def _build_success_result(
        self,
        *,
        worker_input: WorkerInput,
        draft_output: LlmWorkerDraftOutput,
        produced_artifacts: list[ArtifactRef],
        started_at: datetime,
        completed_at: datetime,
    ) -> WorkerResult:
        return WorkerResult(
            schema_version="router.v1",
            task_id=worker_input.task_id,
            worker_job_id=worker_input.worker_job_id,
            worker_type=worker_input.worker_type,
            mcp_tool=worker_input.mcp_tool,
            execution_status=WorkerExecutionStatus.COMPLETED,
            outcome=draft_output.outcome,
            summary=draft_output.summary,
            produced_artifacts=produced_artifacts,
            diagnostics=list(draft_output.diagnostics),
            assumptions=list(draft_output.assumptions),
            failures=_failures_with_evidence(draft_output.failures, produced_artifacts),
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

    def _input_artifact_snapshots(
        self,
        worker_input: WorkerInput,
    ) -> list[McpInputArtifactSnapshot]:
        snapshots: list[McpInputArtifactSnapshot] = []
        for artifact_ref in worker_input.input_artifacts:
            content: str | None = None
            content_truncated = False
            content_chars: int | None = None
            mime_type: str | None = None
            try:
                stored = self.artifact_store.read_artifact_content(artifact_ref.artifact_id)
                decoded = stored.content.decode("utf-8")
                content_truncated = len(decoded) > self.plc_worker_artifact_max_chars
                content = decoded[: self.plc_worker_artifact_max_chars]
                content_chars = len(content)
                mime_type = stored.artifact.storage.mime_type
            except Exception:
                content = None
            snapshots.append(
                McpInputArtifactSnapshot(
                    artifact_id=artifact_ref.artifact_id,
                    type=artifact_ref.type,
                    version=artifact_ref.version,
                    summary=artifact_ref.summary,
                    uri=artifact_ref.uri,
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
        artifact_ids = [artifact.artifact_id for artifact in result.produced_artifacts]
        failure_ids = [failure.failure_id for failure in result.failures]
        return self._build_worker_event(
            worker_input=worker_input,
            event_type=event_type,
            title=f"{_value(worker_input.worker_type)} worker {execution_status}",
            message=result.summary,
            created_at=created_at,
            artifact_ids=artifact_ids or None,
            failure_ids=failure_ids or None,
            severity=severity,
            payload={
                "worker_type": _value(worker_input.worker_type),
                "mcp_tool": _value(worker_input.mcp_tool),
                "worker_job_id": worker_input.worker_job_id,
                "execution_status": execution_status,
                "outcome_status": _value(result.outcome.status),
                "produced_artifact_ids": artifact_ids,
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
            schema_version="router.v1",
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
        if self.mcp_mode == "real":
            return "real"
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


def _artifact_write_request(
    worker_input: WorkerInput,
    intent: MockArtifactWriteIntent,
) -> ArtifactContentWrite:
    return ArtifactContentWrite(
        task_id=worker_input.task_id,
        artifact_type=intent.artifact_type,
        version=intent.version,
        name=intent.name,
        content=intent.content,
        summary=intent.summary,
        visibility=intent.visibility,
        created_by=ArtifactCreator(
            type=ArtifactCreatorType.WORKER,
            worker_type=worker_input.worker_type,
            worker_job_id=worker_input.worker_job_id,
        ),
        metadata=intent.metadata,
        parent_artifact_ids=intent.parent_artifact_ids,
        derived_from_worker_job_id=worker_input.worker_job_id,
        derived_from_artifact_ids=intent.parent_artifact_ids or None,
        mime_type=intent.mime_type,
        artifact_id=new_artifact_id(),
        created_at=utc_now(),
    )


def _failures_with_evidence(
    failures: tuple[Failure, ...],
    produced_artifacts: list[ArtifactRef],
) -> list[Failure]:
    evidence_ids = [
        artifact.artifact_id
        for artifact in produced_artifacts
        if _value(artifact.type) in EVIDENCE_ARTIFACT_TYPES
    ]
    counterexample_id = _first_artifact_id(
        produced_artifacts,
        ArtifactType.COUNTEREXAMPLE,
    )
    updated_failures: list[Failure] = []
    for failure in failures:
        update: dict[str, Any] = {}
        if not failure.evidence_artifact_ids and evidence_ids:
            update["evidence_artifact_ids"] = evidence_ids
        if (
            _value(failure.source) == FailureSource.FORMAL.value
            and counterexample_id is not None
            and failure.reproduction is not None
            and failure.reproduction.counterexample_artifact_id is None
        ):
            update["reproduction"] = failure.reproduction.model_copy(
                update={"counterexample_artifact_id": counterexample_id}
            )
        updated_failures.append(failure.model_copy(update=update) if update else failure)
    return updated_failures


def _first_artifact_id(
    artifacts: list[ArtifactRef],
    artifact_type: ArtifactType,
) -> str | None:
    for artifact in artifacts:
        if _value(artifact.type) == artifact_type.value:
            return artifact.artifact_id
    return None


def _job_status_for_result(result: WorkerResult) -> WorkerJobStatus:
    execution_status = _value(result.execution_status)
    return {
        WorkerExecutionStatus.COMPLETED.value: WorkerJobStatus.COMPLETED,
        WorkerExecutionStatus.PARTIAL.value: WorkerJobStatus.PARTIAL,
        WorkerExecutionStatus.TIMEOUT.value: WorkerJobStatus.TIMEOUT,
        WorkerExecutionStatus.CANCELLED.value: WorkerJobStatus.CANCELLED,
        WorkerExecutionStatus.ERROR.value: WorkerJobStatus.ERROR,
    }[execution_status]


def _input_artifact_ids(worker_input: WorkerInput) -> list[str]:
    return [artifact.artifact_id for artifact in worker_input.input_artifacts]


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
