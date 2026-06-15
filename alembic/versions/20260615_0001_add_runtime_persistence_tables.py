"""add runtime persistence tables

Revision ID: 20260615_0001
Revises:
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260615_0001"
down_revision = None
branch_labels = None
depends_on = None


json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=128), nullable=False),
        sa.Column("difficulty_level", sa.String(length=16), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("state_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.create_index("ix_tasks_status_phase", "tasks", ["status", "phase"])
    op.create_index("ix_tasks_task_type", "tasks", ["task_type"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("visibility", sa.String(length=64), nullable=False),
        sa.Column("storage_provider", sa.String(length=64), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("content_hash", sa.String(length=256), nullable=True),
        sa.Column("summary", sa.String(length=4096), nullable=False),
        sa.Column("artifact_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_status", "artifacts", ["status"])
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])
    op.create_index(
        "ix_artifacts_task_type_version",
        "artifacts",
        ["task_id", "type", "version"],
    )

    op.create_table(
        "events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=64), nullable=False),
        sa.Column("visibility", sa.String(length=64), nullable=False),
        sa.Column("event_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "seq", name="uq_events_task_id_seq"),
    )
    op.create_index("ix_events_task_id_seq", "events", ["task_id", "seq"])
    op.create_index("ix_events_type", "events", ["type"])

    op.create_table(
        "worker_jobs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("worker_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("input_json", json_type, nullable=False),
        sa.Column("result_json", json_type, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="uq_worker_jobs_task_id_idempotency_key",
        ),
    )
    op.create_index("ix_worker_jobs_status", "worker_jobs", ["status"])
    op.create_index("ix_worker_jobs_task_id", "worker_jobs", ["task_id"])
    op.create_index("ix_worker_jobs_worker_type", "worker_jobs", ["worker_type"])

    op.create_table(
        "gate_results",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("gate_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("evidence_artifact_ids", json_type, nullable=False),
        sa.Column("result_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gate_results_gate_type", "gate_results", ["gate_type"])
    op.create_index("ix_gate_results_status", "gate_results", ["status"])
    op.create_index(
        "ix_gate_results_task_id_created_at",
        "gate_results",
        ["task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gate_results_task_id_created_at", table_name="gate_results")
    op.drop_index("ix_gate_results_status", table_name="gate_results")
    op.drop_index("ix_gate_results_gate_type", table_name="gate_results")
    op.drop_table("gate_results")

    op.drop_index("ix_worker_jobs_worker_type", table_name="worker_jobs")
    op.drop_index("ix_worker_jobs_task_id", table_name="worker_jobs")
    op.drop_index("ix_worker_jobs_status", table_name="worker_jobs")
    op.drop_table("worker_jobs")

    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_task_id_seq", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_artifacts_task_type_version", table_name="artifacts")
    op.drop_index("ix_artifacts_task_id", table_name="artifacts")
    op.drop_index("ix_artifacts_status", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_index("ix_tasks_task_type", table_name="tasks")
    op.drop_index("ix_tasks_status_phase", table_name="tasks")
    op.drop_index("ix_tasks_session_id", table_name="tasks")
    op.drop_table("tasks")
