"""Runtime session-store assembly."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url

from coding_agent.config import AgentConfig
from coding_agent.db import create_database_engine, initialize_database
from coding_agent.sessions.mysql import MySqlSessionStore
from coding_agent.sessions.store import JsonlSessionStore, SessionStore


class StorageConfigError(ValueError):
    """Raised when runtime storage configuration cannot be applied safely."""


def create_session_store(config: AgentConfig, data_root: Path) -> SessionStore:
    """Create the configured session store.

    JSONL remains the safe local default. Supplying a database URL selects the
    SQLAlchemy-backed store through AgentConfig normalization.
    """

    if config.storage_backend == "jsonl":
        return JsonlSessionStore(data_root)
    if config.storage_backend != "mysql":
        raise StorageConfigError(f"unsupported storage backend: {config.storage_backend}")
    if config.database_url is None:
        raise StorageConfigError(
            "storage backend 'mysql' requires CODING_AGENT_DATABASE_URL or --database-url"
        )

    raw_url = config.database_url.get_secret_value()
    redacted_url = redact_database_url(raw_url)
    try:
        engine = create_database_engine(
            raw_url,
            pool_size=config.database_pool_size,
            max_overflow=config.database_max_overflow,
            pool_pre_ping=config.database_pool_pre_ping,
            connect_timeout_seconds=config.database_connect_timeout_seconds,
            pool_recycle_seconds=config.database_pool_recycle_seconds,
        )
        if config.database_create_schema:
            initialize_database(engine)
    except Exception as error:
        message = str(error).replace(raw_url, redacted_url)
        password = _database_password(raw_url)
        if password:
            message = message.replace(password, "***")
        raise StorageConfigError(
            f"failed to configure mysql session store for {redacted_url}: {message}"
        ) from error
    return MySqlSessionStore(engine)


def redact_database_url(database_url: str) -> str:
    try:
        url = make_url(database_url)
    except Exception:
        return "[redacted database url]"
    if url.drivername.startswith("sqlite"):
        return url.render_as_string(hide_password=True)
    host = url.host or ""
    if url.port is not None:
        host = f"{host}:{url.port}"
    database = f"/{url.database}" if url.database else ""
    credentials = "***:***@" if url.username or url.password else ""
    return f"{url.drivername}://{credentials}{host}{database}"


def _database_password(database_url: str) -> str:
    try:
        return make_url(database_url).password or ""
    except Exception:
        return ""
