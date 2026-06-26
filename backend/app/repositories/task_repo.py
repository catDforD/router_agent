"""Task state persistence repository."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import RepositoryConflictError, RepositoryNotFoundError
from app.models.db_models import ArtifactRow, EventRow, GateResultRow, TaskRow, WorkerJobRow
from app.models.router_schema import TaskState
from app.repositories._helpers import dump_model, enum_value, flush_or_raise_conflict


class TaskRepository:
    """Repository for complete Router task state payloads."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_task(self, task_state: TaskState) -> TaskState:
        if self.session.get(TaskRow, task_state.task_id) is not None:
            raise RepositoryConflictError(f"task already exists: {task_state.task_id}")

        row = TaskRow(id=task_state.task_id)
        self._apply_task_state(row, task_state)
        self.session.add(row)
        flush_or_raise_conflict(
            self.session,
            f"task already exists: {task_state.task_id}",
        )
        return task_state

    def get_task(self, task_id: str) -> TaskState:
        row = self.session.get(TaskRow, task_id)
        if row is None:
            raise RepositoryNotFoundError(f"task not found: {task_id}")
        return TaskState.model_validate(row.state_json)

    def get_task_for_update(self, task_id: str) -> TaskState:
        row = self.session.execute(
            select(TaskRow)
            .where(TaskRow.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise RepositoryNotFoundError(f"task not found: {task_id}")
        return TaskState.model_validate(row.state_json)

    def list_tasks_by_session(self, session_id: str) -> list[TaskState]:
        rows = self.session.execute(
            select(TaskRow)
            .where(TaskRow.session_id == session_id)
            .order_by(TaskRow.created_at, TaskRow.id)
        ).scalars()
        return [TaskState.model_validate(row.state_json) for row in rows]

    def list_recent_tasks(
        self,
        *,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[TaskState]:
        statement = select(TaskRow)
        if user_id is not None:
            statement = statement.where(TaskRow.user_id == user_id)
        rows = self.session.execute(
            statement.order_by(
                TaskRow.updated_at.desc(),
                TaskRow.created_at.desc(),
                TaskRow.id.desc(),
            ).limit(limit)
        ).scalars()
        return [TaskState.model_validate(row.state_json) for row in rows]

    def update_task_state(self, task_state: TaskState) -> TaskState:
        row = self.session.get(TaskRow, task_state.task_id)
        if row is None:
            raise RepositoryNotFoundError(f"task not found: {task_state.task_id}")

        event_seq = max(row.event_seq, task_state.event_seq)
        if event_seq != task_state.event_seq:
            task_state = task_state.model_copy(update={"event_seq": event_seq})
        self._apply_task_state(row, task_state)
        flush_or_raise_conflict(
            self.session,
            f"task update conflicts with existing data: {task_state.task_id}",
        )
        return task_state

    def delete_task(self, task_id: str) -> None:
        row = self.session.get(TaskRow, task_id)
        if row is None:
            raise RepositoryNotFoundError(f"task not found: {task_id}")
        self.session.execute(delete(GateResultRow).where(GateResultRow.task_id == task_id))
        self.session.execute(delete(WorkerJobRow).where(WorkerJobRow.task_id == task_id))
        self.session.execute(delete(EventRow).where(EventRow.task_id == task_id))
        self.session.execute(delete(ArtifactRow).where(ArtifactRow.task_id == task_id))
        self.session.delete(row)
        self.session.flush()

    @staticmethod
    def _apply_task_state(row: TaskRow, task_state: TaskState) -> None:
        row.session_id = task_state.session_id
        row.user_id = task_state.user_id
        row.status = enum_value(task_state.status)
        row.phase = enum_value(task_state.phase)
        row.task_type = enum_value(task_state.task_type)
        row.difficulty_level = enum_value(task_state.difficulty.level)
        row.event_seq = task_state.event_seq
        row.state_json = dump_model(task_state)
        row.created_at = task_state.created_at
        row.updated_at = task_state.updated_at
        row.started_at = task_state.started_at
        row.completed_at = task_state.completed_at
