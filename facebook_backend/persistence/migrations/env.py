"""Alembic environment.

The URL is never hard-coded in ``alembic.ini``: this file resolves it from
``FACEBOOK_DB_URL`` first, then the ini option, then the default
``data/facebook_backend.db``.  ``render_as_batch`` is enabled because SQLite
cannot ``ALTER TABLE`` most things in place, and future migrations must not be
blocked by that.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

from facebook_backend.persistence.database import create_engine_for, default_database_url
from facebook_backend.persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def resolve_url() -> str:
    return (
        os.environ.get("FACEBOOK_DB_URL")
        or config.get_main_option("sqlalchemy.url")
        or default_database_url()
    )


def run_migrations_offline() -> None:
    context.configure(
        url=resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine_for(resolve_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
