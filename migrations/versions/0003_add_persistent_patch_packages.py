"""add persistent patch packages

Revision ID: 0003_add_persistent_patch_packages
Revises: 0002_add_persistent_approval_queue_fields
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_add_persistent_patch_packages"
down_revision: str | None = "0002_add_persistent_approval_queue_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patches",
        sa.Column("patch_id", sa.String(length=128), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("patch_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "patch_text",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column("patch_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_files", sa.JSON(), nullable=False),
        sa.Column("snapshot_files", sa.JSON(), nullable=False),
        sa.Column("diff_preview", sa.Text(), nullable=False),
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
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invalidated_reason",
            sa.String(length=2000),
            nullable=False,
            server_default="",
        ),
        sa.Column("applied_by", sa.String(length=128), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_patches_session_id", "patches", ["session_id"])
    op.create_index("ix_patches_run_id", "patches", ["run_id"])
    op.create_index("ix_patches_status", "patches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_patches_status", table_name="patches")
    op.drop_index("ix_patches_run_id", table_name="patches")
    op.drop_index("ix_patches_session_id", table_name="patches")
    op.drop_table("patches")
