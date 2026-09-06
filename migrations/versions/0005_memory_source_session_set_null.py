"""relax memories.source_session_id FK to SET NULL

Revision ID: 0005_memory_source_session_set_null
Revises: 0004_add_memory_metadata
Create Date: 2026-09-06

B1-6: deleting a session row must no longer cascade-delete human-reviewed
memories. The FK on ``memories.source_session_id`` changes from
``ondelete=CASCADE`` to ``ondelete=SET NULL`` and the column becomes nullable;
after deletion the memory survives with a NULL source (mapped to "" on read).

SQLite (tests) needs a table rebuild, so the batch branch uses ``copy_from``
with the full target table definition (including the four indexes from 0004).
Batch mode requires every constraint to be named when dropped, so the
``copy_from`` FK carries an explicit name (SQLite never materialises FK names
in DDL, so the name is only internal bookkeeping for alembic).
MySQL (production) uses online ALTER statements instead; there the 0004 FK
was auto-named by the server, hence the inspector lookup.

Downgrade restores NOT NULL, which requires no NULL sources: rows whose
source session was deleted since 0005 are removed first (rare).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_memory_source_session_set_null"
down_revision: str | None = "0004_add_memory_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_memories_source_session"
_FK_NAME_CASCADE = "fk_memories_source_session_cascade"


def _source_session_fk_name() -> str | None:
    """Find the auto-named FK on memories.source_session_id (created in 0004)."""
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys("memories"):
        constrained = foreign_key.get("constrained_columns") or []
        if (
            foreign_key.get("referred_table") == "sessions"
            and "source_session_id" in constrained
        ):
            return foreign_key.get("name")
    return None


def _memories_table(
    source_nullable: bool, ondelete: str, fk_name: str | None
) -> sa.Table:
    """Full memories table definition matching 0004, with a configurable FK."""
    metadata = sa.MetaData()
    return sa.Table(
        "memories",
        metadata,
        sa.Column("memory_id", sa.String(length=128), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "project_id", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column(
            "scope", sa.String(length=64), nullable=False, server_default="session"
        ),
        sa.Column(
            "category", sa.String(length=64), nullable=False, server_default="general"
        ),
        sa.Column(
            "content",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column(
            "source_session_id",
            sa.String(length=128),
            nullable=source_nullable,
        ),
        sa.Column(
            "source_run_id", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="candidate"
        ),
        sa.Column(
            "reviewer", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column("review_note", sa.Text(), nullable=False),
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
            ondelete=ondelete,
            name=fk_name,
        ),
        sa.Index("ix_memories_source_session_id", "source_session_id"),
        sa.Index("ix_memories_status", "status"),
        sa.Index("ix_memories_scope", "scope"),
        sa.Index("ix_memories_user_project_status", "user_id", "project_id", "status"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        source = _memories_table(
            source_nullable=False, ondelete="CASCADE", fk_name=_FK_NAME
        )
        with op.batch_alter_table(
            "memories", copy_from=source, recreate="always"
        ) as batch:
            batch.drop_constraint(_FK_NAME, type_="foreignkey")
            batch.alter_column(
                "source_session_id",
                existing_type=sa.String(length=128),
                nullable=True,
            )
            batch.create_foreign_key(
                _FK_NAME,
                "sessions",
                ["source_session_id"],
                ["session_id"],
                ondelete="SET NULL",
            )
        return

    existing_name = _source_session_fk_name()
    if existing_name is not None:
        op.drop_constraint(existing_name, "memories", type_="foreignkey")
    op.alter_column(
        "memories",
        "source_session_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.create_foreign_key(
        _FK_NAME,
        "memories",
        "sessions",
        ["source_session_id"],
        ["session_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        source = _memories_table(
            source_nullable=True, ondelete="SET NULL", fk_name=_FK_NAME
        )
        with op.batch_alter_table(
            "memories", copy_from=source, recreate="always"
        ) as batch:
            batch.drop_constraint(_FK_NAME, type_="foreignkey")
            batch.alter_column(
                "source_session_id",
                existing_type=sa.String(length=128),
                nullable=False,
            )
            batch.create_foreign_key(
                _FK_NAME_CASCADE,
                "sessions",
                ["source_session_id"],
                ["session_id"],
                ondelete="CASCADE",
            )
        return

    op.drop_constraint(_FK_NAME, "memories", type_="foreignkey")
    # NOT NULL requires a value: drop rows whose source session was deleted.
    op.execute("DELETE FROM memories WHERE source_session_id IS NULL")
    op.alter_column(
        "memories",
        "source_session_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.create_foreign_key(
        None,
        "memories",
        "sessions",
        ["source_session_id"],
        ["session_id"],
        ondelete="CASCADE",
    )
