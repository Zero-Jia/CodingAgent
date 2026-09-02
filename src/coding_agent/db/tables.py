"""SQLAlchemy Core schema for platform storage."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String(128), primary_key=True),
    Column("workspace", Text, nullable=False),
    Column("model_name", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_status", String(64), nullable=False, default="created", server_default="created"),
    Column("last_user_message_preview", Text, nullable=False, default=""),
    Column("message_count", Integer, nullable=False, default=0, server_default="0"),
    Column("run_count", Integer, nullable=False, default=0, server_default="0"),
    Column("tool_count", Integer, nullable=False, default=0, server_default="0"),
    Column("approval_count", Integer, nullable=False, default=0, server_default="0"),
    Column("cancelled_count", Integer, nullable=False, default=0, server_default="0"),
    Column("failed_count", Integer, nullable=False, default=0, server_default="0"),
    Column("total_duration_ms", Float, nullable=False, default=0.0, server_default="0"),
    Column("total_prompt_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("total_completion_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("total_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("current_context_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("context_window_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("context_usage_ratio", Float, nullable=False, default=0.0, server_default="0"),
    Column(
        "current_context_source",
        String(32),
        nullable=False,
        default="estimated",
        server_default="estimated",
    ),
    Column("last_compact_before_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("last_compact_after_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("last_compacted_tokens_saved", Integer, nullable=False, default=0, server_default="0"),
    Column("total_compacted_tokens_saved", Integer, nullable=False, default=0, server_default="0"),
    Column("last_plan_status", String(64), nullable=False, default="", server_default=""),
    Column("last_plan_id", String(128), nullable=False, default="", server_default=""),
    Column("plan_revision_count", Integer, nullable=False, default=0, server_default="0"),
    Column("last_plan_failure", Text, nullable=False, default=""),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("status", String(64), nullable=False, default="running", server_default="running"),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=False, default=""),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

session_events = Table(
    "session_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("event_type", String(128), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("schema_version", Integer, nullable=False),
    Column("workspace", Text, nullable=False),
    Column("model_provider", String(128), nullable=False),
    Column("model_name", String(256), nullable=False),
    Column("messages", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

transcripts = Table(
    "transcripts",
    metadata,
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("content", Text, nullable=False, default=""),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", String(128), primary_key=True),
    Column("schema_version", Integer, nullable=False, default=1, server_default="1"),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("tool_name", String(128), nullable=False),
    Column("reason", String(2000), nullable=False, default="", server_default=""),
    Column("status", String(32), nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("resolution_reason", String(2000), nullable=False, default="", server_default=""),
    Column("resolved_by", String(128), nullable=False, default="", server_default=""),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String(128), primary_key=True),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("path", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

patches = Table(
    "patches",
    metadata,
    Column("patch_id", String(128), primary_key=True),
    Column("schema_version", Integer, nullable=False, default=1, server_default="1"),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("workspace", Text, nullable=False),
    Column("status", String(32), nullable=False, default="pending", server_default="pending"),
    Column("patch_sha256", String(64), nullable=False),
    Column("patch_text", Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
    Column("patch_chars", Integer, nullable=False, default=0, server_default="0"),
    Column("changed_files", JSON, nullable=False),
    Column("snapshot_files", JSON, nullable=False),
    Column("diff_preview", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("applied_at", DateTime(timezone=True), nullable=True),
    Column("invalidated_reason", String(2000), nullable=False, default="", server_default=""),
    Column("applied_by", String(128), nullable=False, default="", server_default=""),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

model_usage = Table(
    "model_usage",
    metadata,
    Column("usage_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "session_id",
        String(128),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "run_id",
        String(128),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("prompt_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("total_tokens", Integer, nullable=False, default=0, server_default="0"),
    Column("estimated_cost_usd", Float, nullable=False, default=0.0, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
)

schema_tables = (
    sessions,
    runs,
    session_events,
    checkpoints,
    transcripts,
    approvals,
    artifacts,
    patches,
    model_usage,
)

__all__ = [
    "approvals",
    "artifacts",
    "checkpoints",
    "metadata",
    "model_usage",
    "patches",
    "runs",
    "schema_tables",
    "session_events",
    "sessions",
    "transcripts",
]
