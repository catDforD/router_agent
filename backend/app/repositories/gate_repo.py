"""Quality gate result persistence repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryNotFoundError
from app.models.db_models import GateResultRow, TaskRow
from app.repositories._helpers import flush_or_raise_conflict


@dataclass(frozen=True)
class GateResultRecord:
    """Internal persisted quality gate result."""

    id: str
    task_id: str
    gate_type: str
    status: str
    blocking: bool
    evidence_artifact_ids: list[str]
    result: dict[str, Any]
    created_at: datetime


class GateResultRepository:
    """Repository for internal quality gate result records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_result(
        self,
        *,
        task_id: str,
        gate_type: str,
        status: str,
        blocking: bool,
        evidence_artifact_ids: list[str],
        result: dict[str, Any],
        created_at: datetime,
        gate_result_id: str | None = None,
    ) -> GateResultRecord:
        if self.session.get(TaskRow, task_id) is None:
            raise RepositoryNotFoundError(f"task not found: {task_id}")

        row = GateResultRow(
            id=gate_result_id or f"gate-result-{uuid4().hex}",
            task_id=task_id,
            gate_type=gate_type,
            status=status,
            blocking=blocking,
            evidence_artifact_ids=evidence_artifact_ids,
            result_json=result,
            created_at=created_at,
        )
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"gate result conflicts with existing data: {row.id}",
        )
        return self._record_from_row(row)

    def list_results(self, task_id: str) -> list[GateResultRecord]:
        rows = self.session.execute(
            select(GateResultRow)
            .where(GateResultRow.task_id == task_id)
            .order_by(GateResultRow.created_at)
        ).scalars()
        return [self._record_from_row(row) for row in rows]

    @staticmethod
    def _record_from_row(row: GateResultRow) -> GateResultRecord:
        return GateResultRecord(
            id=row.id,
            task_id=row.task_id,
            gate_type=row.gate_type,
            status=row.status,
            blocking=row.blocking,
            evidence_artifact_ids=list(row.evidence_artifact_ids),
            result=dict(row.result_json),
            created_at=row.created_at,
        )
