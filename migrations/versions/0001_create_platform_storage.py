"""create platform storage tables

Revision ID: 0001_create_platform_storage
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_create_platform_storage"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(length=128), primary_key=True),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_status", sa.String(length=64), nullable=False, server_default="created"),
        sa.Column("last_user_message_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approval_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_context_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_window_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_usage_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "current_context_source",
            sa.String(length=32),
            nullable=False,
            server_default="estimated",
        ),
        sa.Column("last_compact_before_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_compact_after_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_compacted_tokens_saved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_compacted_tokens_saved", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_plan_status", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_plan_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("plan_revision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_plan_failure", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=128), primary_key=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_runs_session_id", "runs", ["session_id"])
    op.create_table(
        "session_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_session_events_session_id", "session_events", ["session_id"])
    op.create_index("ix_session_events_run_id", "session_events", ["run_id"])
    op.create_index("ix_session_events_event_type", "session_events", ["event_type"])
    op.create_table(
        "checkpoints",
        sa.Column("session_id", sa.String(length=128), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("model_provider", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
    )
    op.create_table(
        "transcripts",
        sa.Column("session_id", sa.String(length=128), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=128), primary_key=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_approvals_session_id", "approvals", ["session_id"])
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=128), primary_key=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifacts_session_id", "artifacts", ["session_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "model_usage",
        sa.Column("usage_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_model_usage_session_id", "model_usage", ["session_id"])
    op.create_index("ix_model_usage_run_id", "model_usage", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_model_usage_run_id", table_name="model_usage")
    op.drop_index("ix_model_usage_session_id", table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_session_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_index("ix_approvals_session_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("transcripts")
    op.drop_table("checkpoints")
    op.drop_index("ix_session_events_event_type", table_name="session_events")
    op.drop_index("ix_session_events_run_id", table_name="session_events")
    op.drop_index("ix_session_events_session_id", table_name="session_events")
    op.drop_table("session_events")
    op.drop_index("ix_runs_session_id", table_name="runs")
    op.drop_table("runs")
    op.drop_table("sessions")
