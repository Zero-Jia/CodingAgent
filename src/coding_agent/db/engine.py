"""SQLAlchemy engine helpers for optional database-backed storage."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from coding_agent.db.tables import metadata


def create_database_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine from an explicit database URL."""
    if not database_url.strip():
        raise ValueError("database_url must be a non-empty string")
    return create_engine(database_url, future=True)


def initialize_database(engine: Engine) -> None:
    """Create the current schema for local development and tests."""
    metadata.create_all(engine)
