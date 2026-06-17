"""Adapter boundary for invoking mock or real MCP PLC workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_artifact_id, new_event_id
from app.core.time import utc_now
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
    execution_error_worker_result,
    normalize_worker_result,
    schema_invalid_worker_result,
    timeout_worker_result,
)


MockRunner = Callable[..., MockWorkerOutput]

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
    ) -> None:
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

    def call_worker(
        self,
        worker_input: WorkerInput,
        *,
        scenario: str | None = None,
    ) -> WorkerResult:
        """Invoke a worker and persist the Router-side audit trail."""

        if self.mcp_mode != "mock":
            raise McpAdapterUnsupportedModeError(
                f"unsupported MCP_MODE for local adapter: {self.mcp_mode!r}"
            )

        active_scenario = scenario or self.mock_scenario
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
                    "mock_scenario": active_scenario,
                },
            )
        )

        try:
            mock_output = self.mock_runner(worker_input, scenario=active_scenario)
            if not isinstance(mock_output, MockWorkerOutput):
                raise MockWorkerSchemaInvalid("mock output is not a MockWorkerOutput")

            produced_refs = self._persist_artifacts(worker_input, mock_output)
            completed_at = utc_now()
            raw_result = self._build_success_result(
                worker_input=worker_input,
                mock_output=mock_output,
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
            return result

        except (MockWorkerSchemaInvalid, WorkerResultNormalizationError) as exc:
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
            return result

        except MockWorkerExecutionError as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

        except Exception as exc:
            return self._complete_execution_error(worker_input, started_at, exc)

    def _persist_artifacts(
        self,
        worker_input: WorkerInput,
        mock_output: MockWorkerOutput,
    ) -> list[ArtifactRef]:
        produced_refs: list[ArtifactRef] = []
        for intent in mock_output.artifact_writes:
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
        return produced_refs

    def _build_success_result(
        self,
        *,
        worker_input: WorkerInput,
        mock_output: MockWorkerOutput,
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
            outcome=mock_output.outcome,
            summary=mock_output.summary,
            produced_artifacts=produced_artifacts,
            diagnostics=list(mock_output.diagnostics),
            assumptions=[],
            failures=_failures_with_evidence(mock_output.failures, produced_artifacts),
            clarification_request=mock_output.clarification_request,
            metrics=mock_output.metrics,
            next_recommended_action=mock_output.next_recommended_action,
            error=None,
            trace_context=worker_input.trace_context.model_copy(
                update={"worker_job_id": worker_input.worker_job_id}
            ),
            started_at=started_at,
            completed_at=completed_at,
            metadata=mock_output.metadata,
        )

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
