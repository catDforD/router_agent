"""add agent sessions

Revision ID: 20260623_0002
Revises: 20260615_0001
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260623_0002"
down_revision = "20260615_0001"
branch_labels = None
depends_on = None


json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("latest_task_id", sa.String(length=128), nullable=True),
        sa.Column("latest_run_id", sa.String(length=128), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(length=4096), nullable=True),
        sa.Column("session_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_sessions_status_updated_at",
        "agent_sessions",
        ["status", "updated_at"],
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])

    op.create_table(
        "agent_session_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=64), nullable=False),
        sa.Column("visibility", sa.String(length=64), nullable=False),
        sa.Column("event_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_session_events_seq"),
    )
    op.create_index(
        "ix_agent_session_events_session_seq",
        "agent_session_events",
        ["session_id", "seq"],
    )
    op.create_index(
        "ix_agent_session_events_task_id",
        "agent_session_events",
        ["task_id"],
    )
    op.create_index("ix_agent_session_events_type", "agent_session_events", ["type"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("user_message", sa.String(length=4096), nullable=False),
        sa.Column("final_response", sa.String(length=12000), nullable=True),
        sa.Column("run_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_runs_session_created_at",
        "agent_runs",
        ["session_id", "created_at"],
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_created_at", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_agent_session_events_type", table_name="agent_session_events")
    op.drop_index("ix_agent_session_events_task_id", table_name="agent_session_events")
    op.drop_index("ix_agent_session_events_session_seq", table_name="agent_session_events")
    op.drop_table("agent_session_events")

    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_status_updated_at", table_name="agent_sessions")
    op.drop_table("agent_sessions")
