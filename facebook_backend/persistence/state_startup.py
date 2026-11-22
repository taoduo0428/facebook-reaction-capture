"""Offline/online ownership, schema validation and manifest cutover for file SQLite."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from facebook_backend.config import Settings
from .database import Database, default_database_path, default_database_url
from .job_store import DatabaseJobStore, ManifestJobStore
from .models import Base, Batch, Job, JobEvent, utc_now

_MIGRATIONS = Path(__file__).resolve().with_name("migrations")
_ALEMBIC_LOCK = Lock()  # env.py uses Alembic's process-wide context proxy.
_RECEIPT = "manifest-cutover-verified-v1"
_LAYOUT = (("api-jobs", "fb_", ".job.json"), ("api-batches", "batch_", ".batch.json"))
_MIGRATE_HINT = "stop the service, back up the database and run `python -m alembic upgrade head`"
_VERIFY_HINT = "stop the service and run legacy_manifest_import --apply and --verify"


def _sqlite_path(url: str) -> Path:
    parsed = make_url(url)
    name = parsed.database
    if (parsed.drivername not in {"sqlite", "sqlite+pysqlite"} or parsed.query or not name
            or name == ":memory:" or name.startswith("file:")):
        raise RuntimeError("the job store requires a local SQLite file URL without query parameters")
    path = Path(name)
    if path.is_symlink() and not path.exists():
        raise RuntimeError("dangling SQLite path")
    path = path.resolve()
    if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
        raise RuntimeError("SQLite requires one regular database file without hard-link aliases")
    return path


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the inode after unlocking. Removing it lets a racing opener lock a
    # different inode. Windows permits a byte-range lock even beyond EOF.
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"state owner lock unavailable: {path}") from exc
        yield  # Closing the handle releases the kernel lock, including on crash.


@contextmanager
def state_owner(root: Path, database_url: str | None = None) -> Iterator[None]:
    """Hold both ownership domains, including a custom database shared across roots."""
    root = Path(root).resolve()
    database = _sqlite_path(database_url or default_database_url(root))
    locks = {root / "data" / ".state-owner.lock", database.with_name(database.name + ".owner.lock")}
    with ExitStack() as stack:
        for path in sorted(locks):
            stack.enter_context(_file_lock(path))
        yield


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS).replace("%", "%%"))
    return config


def validate_schema(database: Database) -> None:
    """Require this release's single Alembic head and its actual core schema."""
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    try:
        with database.engine.connect() as conn:
            context = MigrationContext.configure(conn)
            if not head or context.get_current_heads() != (head,):
                raise RuntimeError(f"expected Alembic head {head}: {_MIGRATE_HINT}")
            changes = compare_metadata(context, Base.metadata)
            if changes:
                raise RuntimeError(f"core schema differs from Alembic head {head}; inspect database offline")
            inspector = inspect(conn)
            for table in Base.metadata.tables.values():
                primary = inspector.get_pk_constraint(table.name)["constrained_columns"]
                if primary != list(table.primary_key.columns.keys()):
                    raise RuntimeError(f"core schema has invalid primary key for {table.name}")
            # Alembic's SQLite index comparison omits the WHERE predicate.
            indexes = inspector.get_indexes("device_leases")
            lease = next((i for i in indexes if i["name"] == "uq_device_leases_active_device"), {})
            predicate = str(lease.get("dialect_options", {}).get("sqlite_where", ""))
            if not lease.get("unique") or " ".join(predicate.lower().split()) != "released_at is null":
                raise RuntimeError("core schema lacks the active-device lease constraint")
            if conn.exec_driver_sql("PRAGMA quick_check(1)").scalar_one() != "ok":
                raise RuntimeError("SQLite integrity check failed")
            if conn.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("SQLite has invalid foreign-key references")
    except SQLAlchemyError as exc:
        raise RuntimeError(f"cannot read the migrated SQLite schema: {_MIGRATE_HINT}") from exc


def _manifest_paths(root: Path) -> list[Path]:
    paths = []
    for directory, prefix, suffix in _LAYOUT:
        parent = root / directory
        if parent.is_symlink() or parent.resolve() != parent:
            raise RuntimeError(f"linked manifest directory: {parent}")
        try:
            entries = sorted(parent.iterdir())
        except FileNotFoundError:
            continue
        for path in entries:
            if not path.name.endswith(suffix):
                continue
            if not path.name.startswith(prefix) or len(path.name) <= len(prefix) + len(suffix):
                # The historical loader globbed ``fb_*.job.json`` /
                # ``batch_*.batch.json``; anything else in these directories was
                # never runtime state (a hand-written note, a stray export).  It is
                # left untouched and stays out of the digest, so a real install
                # with such a file still starts.
                continue
            if path.is_symlink() or path.resolve() != path or not path.is_file():
                raise RuntimeError(f"unexpected legacy manifest path: {path}")
            paths.append(path)
    return paths


def _digest(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def manifest_digest(root: Path) -> str:
    root = Path(root).resolve()
    return _digest(root, _manifest_paths(root))


def write_cutover_receipt(session: Session, root: Path, digest: str) -> None:
    """Record a successfully verified snapshot in the verifier's transaction."""
    payload = json.dumps({"root": str(Path(root).resolve()), "sha256": digest}, sort_keys=True)
    latest = session.scalar(select(JobEvent.payload).where(JobEvent.event_type == _RECEIPT)
                            .order_by(JobEvent.event_id.desc()).limit(1))
    if latest != payload:
        session.add(JobEvent(event_type=_RECEIPT, timestamp=utc_now(), payload=payload))
        session.flush()


def _require_cutover(database: Database, root: Path, paths: list[Path]) -> None:
    with database.session() as session:
        payload = session.scalar(select(JobEvent.payload).where(JobEvent.event_type == _RECEIPT)
                                 .order_by(JobEvent.event_id.desc()).limit(1))
        if payload is None and not paths:
            return
        if payload is None or json.loads(payload) != {"root": str(root), "sha256": _digest(root, paths)}:
            raise RuntimeError(f"missing or stale manifest cutover receipt: {_VERIFY_HINT}")
        # SQLite may progress after cutover, but every migrated identity must survive.
        for (directory, _, suffix), model in zip(_LAYOUT, (Job, Batch), strict=True):
            ids = [p.name.removesuffix(suffix) for p in paths if p.parent.name == directory]
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                found = set(session.scalars(select(model.id).where(model.id.in_(chunk))))
                if found != set(chunk):
                    raise RuntimeError(f"verified manifest identities missing from SQLite: {_VERIFY_HINT}")


def open_state_store(settings: Settings) -> tuple[DatabaseJobStore | ManifestJobStore, Database | None]:
    """Select a state store while the caller owns state_owner through shutdown."""
    root = settings.base_dir.resolve()
    kind = settings.job_store.strip().lower()
    if kind == "manifest":
        return ManifestJobStore(root), None
    if kind not in {"auto", "database"}:
        raise RuntimeError(f"unknown FACEBOOK_JOB_STORE: {kind!r} (expected auto, manifest or database)")
    url = settings.database_url or default_database_url(root)
    path = _sqlite_path(url)
    default_path = default_database_path(root).resolve()
    initialized = default_path.with_name(default_path.name + ".initialized")
    paths = _manifest_paths(root)
    if not path.exists():
        if paths:
            raise RuntimeError(f"legacy manifests require a verified migration: {_VERIFY_HINT}")
        if kind != "auto" or path != default_path:
            raise RuntimeError(f"missing migrated SQLite database: {_MIGRATE_HINT}")
        if os.path.lexists(initialized):
            raise RuntimeError("previously initialized SQLite database is missing; restore its backup")
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("SQLite recovery files exist without their database; inspect offline")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve an absent file exclusively. A failed migration leaves an existing
        # file requiring inspection on the next start, never a silent bootstrap.
        path.touch(exist_ok=False)
        config = _alembic_config()
        config.attributes["database_url"] = url
        with _ALEMBIC_LOCK:
            command.upgrade(config, "head")
    database = Database.open(url)
    try:
        validate_schema(database)
        _require_cutover(database, root, paths)
        if path == default_path and not initialized.exists():
            initialized.touch(exist_ok=False)
        return DatabaseJobStore(database), database
    except BaseException:
        database.dispose()
        raise
