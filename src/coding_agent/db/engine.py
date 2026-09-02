"""SQLAlchemy engine helpers for optional database-backed storage."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from coding_agent.db.tables import metadata


def create_database_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
    connect_timeout_seconds: int = 5,
    pool_recycle_seconds: int = 1800,
) -> Engine:
    """Create a SQLAlchemy engine from an explicit database URL."""
    if not database_url.strip():
        raise ValueError("database_url must be a non-empty string")
    url = make_url(database_url)
    kwargs: dict[str, Any] = {
        "future": True,
        "hide_parameters": True,
    }
    if not url.drivername.startswith("sqlite"):
        kwargs.update(
            {
                "pool_size": pool_size,
                "max_overflow": max_overflow,
                "pool_pre_ping": pool_pre_ping,
                "pool_recycle": pool_recycle_seconds,
            }
        )
    if url.drivername.startswith("mysql"):
        kwargs["connect_args"] = {"connect_timeout": connect_timeout_seconds}
    return create_engine(url, **kwargs)


def initialize_database(engine: Engine) -> None:
    """Create the current schema for local development and tests."""
    metadata.create_all(engine)
