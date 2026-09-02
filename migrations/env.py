"""Alembic environment for CodingAgent database migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

from coding_agent.db.diagnostics import database_connection_error_message
from coding_agent.db.engine import create_database_engine
from coding_agent.db.tables import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    configured = (
        x_args.get("database_url")
        or os.environ.get("CODING_AGENT_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if configured and configured.strip():
        return configured.strip()
    raise RuntimeError(
        "database URL is required for Alembic migrations. Set "
        "CODING_AGENT_DATABASE_URL, pass -x database_url=..., or configure "
        "sqlalchemy.url in alembic.ini."
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = _database_url()
    connectable = None
    try:
        connectable = create_database_engine(database_url)
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    except Exception as error:
        raise RuntimeError(database_connection_error_message(database_url, error)) from error
    finally:
        if connectable is not None:
            connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
