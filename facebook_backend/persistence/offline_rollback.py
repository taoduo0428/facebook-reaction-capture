"""Offline rollback bundle. The SQLite backup and its manifest projection publish together."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from facebook_backend.domain.exceptions import ApiError
from facebook_backend.domain.normalization import normalize_persisted_request
from .database import Database, default_database_url
from .job_store import DatabaseJobStore, ManifestJobStore, batch_payload, job_payload


def checked_id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9_-]+", value) or len(value) > 64:
        raise ValueError(f"invalid {prefix} identifier for a manifest filename")
    return value


def _database_path(root: Path, database_url: str | None) -> Path:
    try:
        url = make_url(database_url or os.environ.get("FACEBOOK_DB_URL") or default_database_url(root))
    except ArgumentError as exc:
        raise ValueError("rollback needs a local file SQLite URL") from exc
    if (url.drivername not in {"sqlite", "sqlite+pysqlite"} or url.query
            or url.host or url.username or url.password or url.port or not url.database
            or url.database == ":memory:" or url.database.startswith("file:")):
        raise ValueError("rollback needs a local file SQLite URL without connection options")
    path = Path(url.database).resolve(strict=True)
    if not path.is_file() or PureWindowsPath(path).drive.startswith("\\\\"):
        raise ValueError("rollback requires a local SQLite database file")
    return path


def _destination(root: Path, destination: Path, database: Path) -> Path:
    destination = Path(destination)
    if not destination.name or destination.name in {".", ".."} or os.path.lexists(destination):
        raise ValueError("rollback destination must be a new directory")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir() or parent != destination.parent.absolute():
        raise ValueError("rollback destination must have an existing unaliased parent")
    target = parent / destination.name
    protected = [root / name for name in ("api-jobs", "api-batches", "data", "runs", ".git")]
    if (any(target.is_relative_to(path) for path in protected)
            or database.is_relative_to(target) or root.is_relative_to(target)):
        raise ValueError("rollback destination overlaps active state")
    return target


def _check_run_path(value: Any, root: Path) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid run path")
    path = Path(value)
    win = PureWindowsPath(value)
    if (".." in path.parts or ".." in win.parts or win.drive.startswith("\\\\")
            or (not path.is_absolute() and (win.anchor or "\\" in value and os.name != "nt"))
            or any(":" in part for part in win.parts if part != win.anchor)):
        raise ValueError("invalid run path")
    resolved = (root / path).resolve()
    if resolved == root / "runs" or not resolved.is_relative_to(root / "runs"):
        raise ValueError("run path outside source runs directory")


def _publish(stage: Path, target: Path) -> None:
    """Atomic rename with no replacement, even if an empty target appeared after preflight."""
    if os.name == "nt":
        os.rename(stage, target)  # On Windows an existing destination always fails.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(stage), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(os.fsencode(stage), os.fsencode(target), 4)  # RENAME_EXCL
    else:
        raise RuntimeError("filesystem platform has no supported atomic exclusive directory rename")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _backup(source: Path, backup: Path) -> None:
    # mode=ro still reads committed WAL frames. immutable=1 would miss those frames.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM sqlite_schema").fetchone()  # anchor the snapshot
        with closing(sqlite3.connect(backup)) as writer:
            reader.backup(writer)


def _check_idle(db: sqlite3.Connection) -> None:
    checks = (
        ("SELECT 1 FROM device_leases WHERE released_at IS NULL LIMIT 1", "open device lease"),
        ("SELECT 1 FROM job_attempts WHERE finished_at IS NULL LIMIT 1", "unfinished attempt"),
        ("SELECT 1 FROM devices WHERE current_job_id IS NOT NULL OR cooldown_until > unixepoch() LIMIT 1",
         "active device assignment or cooldown"),
    )
    for sql, reason in checks:
        if db.execute(sql).fetchone():
            raise ValueError(f"rollback blocked by {reason}")


# Files consumed by build_api_result and the legacy result loader. Capture logs
# contribute only their presence/path. Raw captures are never copied or parsed.
_RESULT_FILES = (
    "analysis/facebook-sample-patterns.json", "analysis/facebook-reactor-users.csv",
    "analysis/facebook-share-users.csv", "target-open/target-open-diagnostics.json",
    "readonly-ui/exploration-diagnostics.json", "readonly-ui-fallback/exploration-diagnostics.json",
    "facebook-live-progress.json", "capture.log",
)


def _identity(st: os.stat_result) -> tuple:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _file_identity(path: Path) -> tuple | None:
    if path.resolve() != path:
        raise ValueError("linked result evidence")
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError("result evidence is not a single regular file")
    return _identity(st)


class _ResultEvidence:
    def __init__(self, root: Path, scratch: Path):
        self.root = root
        self.scratch = scratch
        self.signatures: dict[Path, tuple | None] = {}

    def _snapshot_dir(self, source: Path) -> Path:
        _check_run_path(str(source), self.root)
        directory = self.scratch / source.relative_to(self.root)
        directory.mkdir(parents=True, exist_ok=True)
        for name in _RESULT_FILES:
            path = source / name
            if path in self.signatures:
                continue
            signature = _file_identity(path)
            self.signatures[path] = signature
            if signature is None:
                continue
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == 'capture.log':
                target.touch(exist_ok=False)
            else:
                with path.open('rb') as stream, target.open('xb') as output:
                    if _identity(os.fstat(stream.fileno())) != signature:
                        raise ValueError("result evidence changed before copy")
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                    if _identity(os.fstat(stream.fileno())) != signature:
                        raise ValueError("result evidence changed during copy")
        return directory

    def check_unchanged(self) -> None:
        if any(_file_identity(path) != signature for path, signature in self.signatures.items()):
            raise ValueError("result evidence changed during rollback")

    def verify(self, job: dict, expected: str | None, request: dict) -> None:
        import facebook_capture_api as api

        if not job['run_dir']:
            raise ValueError("completed job has no result directory")
        run = self.root / job['run_dir']
        # Resolve relative fields against the installation, without altering the
        # process-wide API base dir. Match JobManager.result's evidence order.
        absolute_job = dict(job, run_dir=str(run), capture_run_dir=str(self.root / (job['capture_run_dir'] or run)),
                            attempt_dirs=[str(self.root / path) for path in job['attempt_dirs']])
        candidates = api._job_evidence_dirs(absolute_job)
        copies = [self._snapshot_dir(directory) for directory in candidates]
        private_run = self.scratch / run.relative_to(self.root)
        if api._select_evidence_dir(private_run, copies) is None:
            raise ValueError("completed job lacks a complete result evidence directory")
        try:
            rebuilt = api.build_api_result(private_run, request, evidence_dirs=copies)
        except (ApiError, AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("completed result rejected by the capture validator") from exc
        rebuilt['run_dir'] = str(run)
        for directory, copy in zip(candidates, copies, strict=True):
            if (copy / 'capture.log').is_file():
                rebuilt['job_log'] = str(directory / 'capture.log')
                break
        if job['completion_scope'] is not None and job['completion_scope'] != rebuilt['completion_scope']:
            raise ValueError("completed result has a different completion scope")
        if expected is not None:
            # Serializing avoids Python treating True == 1 when comparing JSON.
            saved = json.dumps(json.loads(expected), sort_keys=True, allow_nan=False)
            if saved != json.dumps(rebuilt, sort_keys=True, allow_nan=False):
                raise ValueError("persisted result differs from reconstruction in manifest mode")


def _project_job(row: dict[str, Any], store: DatabaseJobStore, root: Path, evidence: _ResultEvidence) -> dict[str, Any]:
    job_id = checked_id(row["id"], "fb")
    payload = json.loads(row["payload"])
    if not isinstance(payload, dict):
        raise ValueError(f"{job_id}: invalid persisted job payload")
    completed = row["status"] == "completed"
    if row["pinned_device_id"] is not None or (row["result"] is not None and not completed):
        raise ValueError(f"{job_id}: manifest cannot preserve device pin or non-completed result")
    cleanup = row["transport_cleanup_state"]
    runner_finished = (row["process_state"] == "stopped" and bool(row["finished_at"]))
    if (row["status"] not in {"queued", "failed", "cancelled", "completed"}
            or row["process_pid"] is not None or row["process_creation_time"] is not None
            or row["process_state"] not in (None, "not-started", "stopped")
            or (cleanup not in (None, "restored", "not-required") and not (cleanup == 'runner-owned' and runner_finished))
            or payload.get("transport_cleanup_error")
            or (row["transport_cleanup_state"] is None and (row["attempt_count"] or row["started_at"]))):
        raise ValueError(f"{job_id}: unresolved execution or transport cleanup")
    recovery = row["recovery_state"] or ""
    if (recovery == "failed-result-persistence" or any(word in recovery for word in
            ("pending", "unverified", "blocked", "still-running", "no-auto-resume", "registration-race"))):
        raise ValueError(f"{job_id}: unresolved recovery state")
    if not isinstance(payload.get("attempt_dirs", []), list):
        raise ValueError(f"{job_id}: invalid attempt_dirs")
    for value in (row["run_dir"], row["capture_run_dir"], *payload.get("attempt_dirs", [])):
        _check_run_path(value, root)
    try:
        normalize_persisted_request(payload.get("request"))
    except (ApiError, TypeError, ValueError) as exc:
        raise ValueError(f"{job_id}: request cannot load in manifest mode") from exc
    job = store._job_from_row(dict(row, payload=payload))
    if row["error_code"] or (row["error_message"] is not None and payload.get("error") is None):
        raise ValueError(f"{job_id}: database-only error fields")
    key = job.get("submission_key")
    if key is not None and not isinstance(key, str):
        raise ValueError(f"{job_id}: invalid submission key")
    return job_payload(job)


def _projection(backup: Path, database: Database, root: Path) -> tuple[dict, dict]:
    store = DatabaseJobStore(database)
    jobs: dict[str, dict] = {}
    batches: dict[str, dict] = {}
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("inconsistent SQLite backup")
        _check_idle(db)
        keys: set[str] = set()
        for row in db.execute("SELECT * FROM jobs ORDER BY id"):
            job = _project_job(dict(row), store, root)
            key = job["submission_key"]
            if key and key in keys:
                raise ValueError("duplicate submission key in SQLite backup")
            if key:
                keys.add(key)
            jobs[row["id"]] = job
        links = {(r[0], r[1], r[2]) for r in db.execute("SELECT batch_id, position, job_id FROM batch_jobs")}
        expected = {(j["batch_id"], j["batch_index"], jid) for jid, j in jobs.items()
                    if j["batch_id"] is not None or j["batch_index"] is not None}
        if links != expected:
            raise ValueError("inconsistent SQLite batch membership")
        for row in db.execute("SELECT * FROM batches ORDER BY id"):
            bid = checked_id(row["id"], "batch")
            members = db.execute("SELECT position, job_id FROM batch_jobs WHERE batch_id=? ORDER BY position", (bid,)).fetchall()
            if (not members or row["total_jobs"] != len(members)
                    or [pos for pos, _ in members] != list(range(1, len(members) + 1))):
                raise ValueError(f"{bid}: incomplete batch")
            ids = [jid for _, jid in members]
            counts = Counter(jobs[jid]["status"] for jid in ids)
            if any(counts[status] != row[status + "_jobs"] for status in ("completed", "failed", "cancelled")):
                raise ValueError(f"{bid}: inconsistent batch totals")
            _check_run_path(row["run_dir"], root)
            batches[bid] = batch_payload(dict(row, batch_id=bid, job_ids=ids))
    return jobs, batches


def _write_and_check(stage: Path, jobs: dict, batches: dict) -> None:
    store = ManifestJobStore(stage)
    for docs, folder, path_for, loader in (
        (jobs, store.jobs_dir, store.job_path, store.load_job_manifests),
        (batches, store.batches_dir, store.batch_path, store.load_batch_manifests),
    ):
        folder.mkdir()
        if len({key.casefold() for key in docs}) != len(docs):
            raise ValueError("manifest filenames collide on a case-insensitive filesystem")
        for identifier, doc in docs.items():
            with path_for(identifier).open("x", encoding="utf-8", newline="\n") as output:
                json.dump(doc, output, ensure_ascii=False, allow_nan=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        if {key: doc for doc, key in loader()} != docs:
            raise ValueError("manifest read-back differs from SQLite snapshot")


def rollback_from_sqlite(root: Path, destination: Path, *, database_url: str | None = None) -> dict[str, Any]:
    """Create one verified backup and legacy manifests. Switching stores remains explicit."""
    from .state_startup import state_owner, validate_schema

    root = Path(root).resolve(strict=True)
    source = _database_path(root, database_url)
    target = _destination(root, destination, source)
    if not root.is_dir():
        raise ValueError("rollback source root must be an existing directory")
    url = f"sqlite+pysqlite:///{source.as_posix()}"
    with state_owner(root, url), tempfile.TemporaryDirectory(prefix=".manifest-rollback-", dir=target.parent) as temp:
        # Publish a child of the private directory so TemporaryDirectory never owns the final target.
        stage = Path(temp) / "bundle"
        stage.mkdir(mode=0o700)
        backup = stage / "database.sqlite3"
        try:
            _backup(source, backup)
            with Database.open(f"sqlite+pysqlite:///{backup.as_posix()}") as snapshot:
                validate_schema(snapshot)
                jobs, batches = _projection(backup, snapshot, root)
            _write_and_check(stage, jobs, batches)
        except (sqlite3.Error, SQLAlchemyError) as exc:
            raise ValueError("could not read or validate SQLite rollback snapshot") from exc
        _publish(stage, target)
    return {"ok": True, "mode": "rollback", "database": str(source), "backup": str(target / backup.name),
            "destination": str(target), "jobs_written": len(jobs), "batches_written": len(batches)}
