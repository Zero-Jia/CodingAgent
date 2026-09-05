"""add memory metadata table

Revision ID: 0004_add_memory_metadata
Revises: 0003_add_persistent_patch_packages
Create Date: 2026-09-05

Introduces the ``memories`` table that stores memory records extracted from
sessions and pending human review. This revision only adds the metadata
schema; vector collection, extraction and recall injection are handled in
later phases (B1-2 ~ B1-6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004_add_memory_metadata"
down_revision: str | None = "0003_add_persistent_patch_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("memory_id", sa.String(length=128), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "scope",
            sa.String(length=64),
            nullable=False,
            server_default="session",
        ),
        sa.Column(
            "category",
            sa.String(length=64),
            nullable=False,
            server_default="general",
        ),
        sa.Column(
            "content",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column("source_session_id", sa.String(length=128), nullable=False),
        sa.Column(
            "source_run_id",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="0.5",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column(
            "reviewer",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "review_note",
            sa.Text(),
            nullable=False,
        ),
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
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_session_id"],
            ["sessions.session_id"],
            ondelete="CASCADE",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_memories_source_session_id",
        "memories",
        ["source_session_id"],
    )
    op.create_index("ix_memories_status", "memories", ["status"])
    op.create_index("ix_memories_scope", "memories", ["scope"])
    op.create_index(
        "ix_memories_user_project_status",
        "memories",
        ["user_id", "project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memories_user_project_status",
        table_name="memories",
    )
    op.drop_index("ix_memories_scope", table_name="memories")
    op.drop_index("ix_memories_status", table_name="memories")
    op.drop_index(
        "ix_memories_source_session_id",
        table_name="memories",
    )
    op.drop_table("memories")
