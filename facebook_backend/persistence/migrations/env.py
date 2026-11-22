"""Alembic environment.

A programmatic database_url (or explicit ini URL) takes precedence over the
shell's FACEBOOK_DB_URL. Command-line runs with the shipped empty option use
the environment or the project default. ``render_as_batch`` supports SQLite.
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
        config.attributes.get("database_url")
        or config.get_main_option("sqlalchemy.url")
        or os.environ.get("FACEBOOK_DB_URL")
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
