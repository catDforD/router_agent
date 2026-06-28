"""Worker job persistence repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError, RepositoryNotFoundError
from app.models.db_models import WorkerJobRow
from app.models.router_schema import WorkerInput, WorkerJobStatus, WorkerResult
from app.repositories._helpers import (
    dump_model,
    enum_value,
    flush_or_raise_conflict,
    sanitize_legacy_worker_input_payload,
    sanitize_legacy_worker_result_payload,
)


@dataclass(frozen=True)
class WorkerJobRecord:
    """Validated worker job record returned by the repository."""

    id: str
    task_id: str
    worker_type: str
    status: str
    idempotency_key: str
    input: WorkerInput
    result: WorkerResult | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkerJobRepository:
    """Repository for worker job lifecycle state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_job(
        self,
        worker_input: WorkerInput,
        status: WorkerJobStatus = WorkerJobStatus.RUNNING,
        started_at: datetime | None = None,
    ) -> WorkerJobRecord:
        if self.session.get(WorkerJobRow, worker_input.worker_job_id) is not None:
            raise RepositoryConflictError(
                f"worker job already exists: {worker_input.worker_job_id}"
            )

        row = WorkerJobRow(
            id=worker_input.worker_job_id,
            task_id=worker_input.task_id,
            worker_type=enum_value(worker_input.worker_type),
            status=enum_value(status),
            idempotency_key=worker_input.idempotency_key,
            input_json=dump_model(worker_input),
            result_json=None,
            started_at=started_at or worker_input.created_at,
            completed_at=None,
            created_at=worker_input.created_at,
            updated_at=worker_input.created_at,
        )
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"worker job conflicts with existing data: {worker_input.worker_job_id}",
        )
        return self._record_from_row(row)

    def get_job(self, worker_job_id: str) -> WorkerJobRecord:
        row = self.session.get(WorkerJobRow, worker_job_id)
        if row is None:
            raise RepositoryNotFoundError(f"worker job not found: {worker_job_id}")
        return self._record_from_row(row)

    def list_task_jobs(self, task_id: str) -> list[WorkerJobRecord]:
        rows = self.session.execute(
            select(WorkerJobRow)
            .where(WorkerJobRow.task_id == task_id)
            .order_by(WorkerJobRow.created_at, WorkerJobRow.id)
        ).scalars()
        return [self._record_from_row(row) for row in rows]

    def complete_job(
        self,
        worker_job_id: str,
        result: WorkerResult,
        status: WorkerJobStatus = WorkerJobStatus.COMPLETED,
    ) -> WorkerJobRecord:
        row = self.session.get(WorkerJobRow, worker_job_id)
        if row is None:
            raise RepositoryNotFoundError(f"worker job not found: {worker_job_id}")
        if result.worker_job_id != row.id:
            raise RepositoryConflictError(
                f"worker result belongs to {result.worker_job_id}, not {row.id}"
            )
        if result.task_id != row.task_id:
            raise RepositoryConflictError(
                f"worker result task {result.task_id} does not match {row.task_id}"
            )

        row.status = enum_value(status)
        row.result_json = dump_model(result)
        row.completed_at = result.completed_at
        row.updated_at = result.completed_at
        flush_or_raise_conflict(
            self.session,
            f"worker job completion conflicts with existing data: {worker_job_id}",
        )
        return self._record_from_row(row)

    @staticmethod
    def _record_from_row(row: WorkerJobRow) -> WorkerJobRecord:
        return WorkerJobRecord(
            id=row.id,
            task_id=row.task_id,
            worker_type=row.worker_type,
            status=row.status,
            idempotency_key=row.idempotency_key,
            input=WorkerInput.model_validate(
                sanitize_legacy_worker_input_payload(row.input_json)
            ),
            result=(
                WorkerResult.model_validate(
                    sanitize_legacy_worker_result_payload(row.result_json)
                )
                if row.result_json is not None
                else None
            ),
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
