"""MCP worker result validation and error normalization."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import ValidationError

from app.models.router_schema import (
    NextRecommendedAction,
    WorkerError,
    WorkerExecutionStatus,
    WorkerInput,
    WorkerMetrics,
    WorkerOutcome,
    WorkerOutcomeStatus,
    WorkerResult,
)


ERROR_MCP_TIMEOUT = "MCP_TIMEOUT"
ERROR_WORKER_SCHEMA_INVALID = "WORKER_SCHEMA_INVALID"
ERROR_WORKER_EXECUTION_ERROR = "WORKER_EXECUTION_ERROR"


class WorkerResultNormalizationError(Exception):
    """Raised when worker output cannot be accepted as a valid result."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = dict(details or {})


def normalize_worker_result(
    raw_result: WorkerResult | dict[str, Any],
    worker_input: WorkerInput,
) -> WorkerResult:
    """Validate raw worker output and ensure it belongs to the original input."""

    try:
        result = (
            raw_result
            if isinstance(raw_result, WorkerResult)
            else WorkerResult.model_validate(raw_result)
        )
    except ValidationError as exc:
        raise WorkerResultNormalizationError(
            ERROR_WORKER_SCHEMA_INVALID,
            "worker output is not a valid WorkerResult",
            details={"validation_error": str(exc)},
        ) from exc

    mismatches: dict[str, dict[str, str]] = {}
    _record_mismatch(
        mismatches,
        "task_id",
        expected=worker_input.task_id,
        actual=result.task_id,
    )
    _record_mismatch(
        mismatches,
        "worker_job_id",
        expected=worker_input.worker_job_id,
        actual=result.worker_job_id,
    )
    _record_mismatch(
        mismatches,
        "worker_type",
        expected=_value(worker_input.worker_type),
        actual=_value(result.worker_type),
    )
    _record_mismatch(
        mismatches,
        "mcp_tool",
        expected=_value(worker_input.mcp_tool),
        actual=_value(result.mcp_tool),
    )
    if mismatches:
        raise WorkerResultNormalizationError(
            ERROR_WORKER_SCHEMA_INVALID,
            "worker output identity does not match WorkerInput",
            details={"mismatches": mismatches},
        )

    return result


def timeout_worker_result(
    worker_input: WorkerInput,
    *,
    started_at: datetime,
    completed_at: datetime,
    message: str,
) -> WorkerResult:
    """Build a standard timeout WorkerResult for a worker input."""

    return _error_worker_result(
        worker_input,
        execution_status=WorkerExecutionStatus.TIMEOUT,
        error_code=ERROR_MCP_TIMEOUT,
        message=message,
        retryable=True,
        next_action=NextRecommendedAction.RETRY,
        started_at=started_at,
        completed_at=completed_at,
    )


def schema_invalid_worker_result(
    worker_input: WorkerInput,
    *,
    started_at: datetime,
    completed_at: datetime,
    message: str,
    details: dict[str, Any] | None = None,
) -> WorkerResult:
    """Build a standard schema-invalid WorkerResult."""

    return _error_worker_result(
        worker_input,
        execution_status=WorkerExecutionStatus.ERROR,
        error_code=ERROR_WORKER_SCHEMA_INVALID,
        message=message,
        retryable=False,
        next_action=NextRecommendedAction.RETRY,
        started_at=started_at,
        completed_at=completed_at,
        details=details,
    )


def execution_error_worker_result(
    worker_input: WorkerInput,
    *,
    started_at: datetime,
    completed_at: datetime,
    message: str,
    details: dict[str, Any] | None = None,
) -> WorkerResult:
    """Build a standard execution-error WorkerResult."""

    return _error_worker_result(
        worker_input,
        execution_status=WorkerExecutionStatus.ERROR,
        error_code=ERROR_WORKER_EXECUTION_ERROR,
        message=message,
        retryable=True,
        next_action=NextRecommendedAction.RETRY,
        started_at=started_at,
        completed_at=completed_at,
        details=details,
    )


def _error_worker_result(
    worker_input: WorkerInput,
    *,
    execution_status: WorkerExecutionStatus,
    error_code: str,
    message: str,
    retryable: bool,
    next_action: NextRecommendedAction,
    started_at: datetime,
    completed_at: datetime,
    details: dict[str, Any] | None = None,
) -> WorkerResult:
    return WorkerResult(
        schema_version="router.v1",
        task_id=worker_input.task_id,
        worker_job_id=worker_input.worker_job_id,
        worker_type=worker_input.worker_type,
        mcp_tool=worker_input.mcp_tool,
        execution_status=execution_status,
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.UNKNOWN,
            blocking=True,
            confidence=0.0,
            reason=message,
        ),
        summary=message,
        produced_artifacts=[],
        diagnostics=[],
        assumptions=[],
        failures=[],
        metrics=WorkerMetrics(),
        next_recommended_action=next_action,
        error=WorkerError(
            error_code=error_code,
            message=message,
            retryable=retryable,
            details=details,
        ),
        trace_context=worker_input.trace_context.model_copy(
            update={"worker_job_id": worker_input.worker_job_id}
        ),
        started_at=started_at,
        completed_at=completed_at,
        metadata={"normalized_error": error_code},
    )


def _record_mismatch(
    mismatches: dict[str, dict[str, str]],
    field: str,
    *,
    expected: str,
    actual: str,
) -> None:
    if expected != actual:
        mismatches[field] = {"expected": expected, "actual": actual}


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
