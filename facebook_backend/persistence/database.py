"""Engine and session lifecycle.

Rules this module enforces (and the tests check):

* **SQLite pragmas on every connection**: ``journal_mode=WAL`` (readers never
  block the writer), ``foreign_keys=ON`` (SQLite ignores FKs by default),
  ``busy_timeout`` (bounded wait instead of an instant "database is locked"),
  ``synchronous=NORMAL`` (the correct durability/speed point for WAL).
* **One session per unit of work.**  ``Database.session()`` creates a session,
  commits on success, rolls back on any exception and always closes it.  No
  module-level session, no session shared between threads.
* **Explicit transactions.**  ``autoflush=False`` so nothing is written to the
  database behind the caller's back; repositories flush/commit deliberately.

The database file lives under ``data/`` next to the project; the manifests stay
in ``api-jobs/`` and ``api-batches/`` until the offline import has been verified.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from facebook_backend.config import PROJECT_ROOT
from facebook_backend.persistence.models import Base

DEFAULT_DB_FILENAME = "facebook_backend.db"
DEFAULT_BUSY_TIMEOUT_MS = 30_000


def default_database_path(base_dir: Path | None = None) -> Path:
    return (base_dir or PROJECT_ROOT) / "data" / DEFAULT_DB_FILENAME


def default_database_url(base_dir: Path | None = None) -> str:
    return f"sqlite+pysqlite:///{default_database_path(base_dir).as_posix()}"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine_for(
    url: str,
    *,
    echo: bool = False,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Engine:
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if _is_sqlite(url):
        # A pooled connection may be handed to another worker thread, so the
        # per-thread check must be relaxed; the session lifecycle above is what
        # keeps threads from sharing a Session.
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": busy_timeout_ms / 1000,
        }
    engine = create_engine(url, **kwargs)
    if _is_sqlite(url):
        _install_sqlite_pragmas(engine, busy_timeout_ms)
    return engine


def _install_sqlite_pragmas(engine: Engine, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()
        except BaseException:
            # The pool does not own the connection until this hook succeeds.
            dbapi_connection.close()
            raise


class Database:
    """Owns one engine and hands out short-lived sessions."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
            future=True,
        )

    @classmethod
    def open(
        cls,
        url: str | None = None,
        *,
        base_dir: Path | None = None,
        echo: bool = False,
    ) -> "Database":
        resolved = url or default_database_url(base_dir)
        database = cls(create_engine_for(resolved, echo=echo))
        if database.is_sqlite and database.database_path() not in {"", ":memory:"}:
            Path(database.database_path()).parent.mkdir(parents=True, exist_ok=True)
        return database

    @property
    def is_sqlite(self) -> bool:
        return self.engine.url.get_backend_name() == "sqlite"

    def database_path(self) -> str:
        return str(self.engine.url.database or "")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One transaction per unit of work; always closes the session."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_all(self) -> None:
        """Create the schema directly (tests and local bootstrap only).

        Production uses ``alembic upgrade head`` so the schema has a recorded
        revision; ``create_all`` is not a substitute for migrations.
        """
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc) -> None:
        self.dispose()


def sqlite_pragmas(database: Database) -> dict[str, str]:
    """Read back the pragmas actually in effect (used by the tests/doctor)."""
    with database.engine.connect() as connection:
        return {
            "journal_mode": str(connection.execute(text("PRAGMA journal_mode")).scalar()),
            "foreign_keys": str(connection.execute(text("PRAGMA foreign_keys")).scalar()),
            "busy_timeout": str(connection.execute(text("PRAGMA busy_timeout")).scalar()),
            "synchronous": str(connection.execute(text("PRAGMA synchronous")).scalar()),
        }


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_DB_FILENAME",
    "Database",
    "create_engine_for",
    "default_database_path",
    "default_database_url",
    "sqlite_pragmas",
]
