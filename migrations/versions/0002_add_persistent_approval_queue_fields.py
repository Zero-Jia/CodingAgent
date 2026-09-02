"""add persistent approval queue fields

Revision ID: 0002_add_persistent_approval_queue_fields
Revises: 0001_create_platform_storage
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_persistent_approval_queue_fields"
down_revision: str | None = "0001_create_platform_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("reason", sa.String(length=2000), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "resolution_reason",
                sa.String(length=2000),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column("resolved_by", sa.String(length=128), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.drop_column("resolved_by")
        batch.drop_column("resolution_reason")
        batch.drop_column("expires_at")
        batch.drop_column("reason")
        batch.drop_column("schema_version")
