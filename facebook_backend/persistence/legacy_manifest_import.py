"""Offline v1 manifest migration. Dry-run validates the source, verify reads
source and target, and apply reconciles inside a single write transaction.
Use ``python -m facebook_backend.persistence.legacy_manifest_import --help``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from sqlalchemy import create_engine, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from facebook_backend.artifacts.catalog import ARTIFACT_SPECS, REQUIRED_ARTIFACTS
from facebook_backend.artifacts.manager import checked_artifact_path
from facebook_backend.domain.exceptions import ApiError
from facebook_backend.persistence.database import Database, default_database_url
from facebook_backend.persistence.job_store import DatabaseJobStore, batch_payload, job_payload
from facebook_backend.persistence.models import BatchJob, DeviceLease, Job, utc_now
from facebook_backend.persistence.repositories import (
    ArtifactRepository, BatchRepository, EventRepository, JobRepository,
)
from facebook_backend.platform import process_manager

JOB_GLOB = "fb_*.job.json"
BATCH_GLOB = "batch_*.batch.json"
ALLOWED_JOB_STATUSES = frozenset({"queued", "running", "cancelling", "completed", "failed", "cancelled"})
OFFLINE_PROCESS_STATES = (None, "not-started", "stopped")
OFFLINE_CLEANUP_STATES = (None, "restored", "not-required", "runner-owned")
DEFAULT_API_PORT = 8765
JOB_MANIFEST_FIELDS = job_payload({}).keys() | {"pinned_device_id", "result", "error_code"}
BATCH_MANIFEST_FIELDS = batch_payload({}).keys() | {"status"}


@dataclass(frozen=True)
class ArtifactEntry:
    name: str
    path: str
    size: int
    sha256: str
    content_type: str | None

    def fields(self) -> dict[str, Any]:
        return dict(artifact_name=self.name, artifact_path=self.path, size=self.size,
                    sha256=self.sha256, content_type=self.content_type, verification_status="verified")


@dataclass(frozen=True)
class JobEntry:
    job_id: str
    path: Path
    manifest: dict[str, Any]
    status: str
    identity: str
    process_evidence: bool
    safe_to_resume: bool
    blocked_reason: str | None
    artifacts: tuple[ArtifactEntry, ...]
    missing_required: tuple[str, ...]


@dataclass(frozen=True)
class BatchEntry:
    batch_id: str
    path: Path
    manifest: dict[str, Any]
    job_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImportPlan:
    root: Path
    digest: str
    jobs: tuple[JobEntry, ...]
    batches: tuple[BatchEntry, ...]
    invalid_jobs: tuple[dict, ...]
    invalid_batches: tuple[dict, ...]
    orphan_batches: tuple[dict, ...]
    duplicate_references: tuple[dict, ...]
    orphaned_job_batch_references: tuple[dict, ...]
    blocked: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return not any((self.invalid_jobs, self.invalid_batches, self.orphan_batches,
                        self.duplicate_references, self.orphaned_job_batch_references, self.blocked))


def _collect_artifacts(root: Path, value: str | None) -> tuple[tuple[ArtifactEntry, ...], tuple[str, ...]]:
    found = []
    if value:
        run_dir = (root / value).resolve()
        if not run_dir.is_relative_to(root):
            raise ValueError("run_dir resolves outside source root")
        for name, _label, _layer, relative in ARTIFACT_SPECS:
            try:
                path = checked_artifact_path(run_dir, Path(relative))
            except ApiError:
                candidate = run_dir / relative
                if candidate.is_symlink() or candidate.resolve() != candidate:
                    raise ValueError(f"linked artifact: {name}")
                continue
            with path.open("rb") as handle:
                before = os.fstat(handle.fileno())
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
                after = os.fstat(handle.fileno())
            current = path.stat()
            if any((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) !=
                   (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                   for s in (after, current)):
                raise ValueError(f"artifact changed while hashing: {name}")
            size = before.st_size
            found.append(ArtifactEntry(name, str(path), size, digest,
                                       "text/csv; charset=utf-8" if path.suffix.lower() == ".csv" else None))
    return tuple(found), tuple(sorted(REQUIRED_ARTIFACTS - {item.name for item in found}))


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def _load(path: Path, root: Path, key: str, suffix: str) -> dict:
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("manifest path is outside source or is a symlink")
    raw = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_json_object)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("manifest version is not 1")
    if raw.get(key) != path.name.removesuffix(suffix):
        raise ValueError(f"{key} does not match the file name")
    allowed = (JOB_MANIFEST_FIELDS if key == "job_id" else BATCH_MANIFEST_FIELDS)
    if unknown := raw.keys() - allowed:
        raise ValueError(f"unmapped manifest fields: {sorted(unknown)}")
    if not isinstance(raw.get("created_at"), str) or not raw["created_at"].strip():
        raise ValueError("missing created_at")
    return raw


def _offline_state(raw: dict, probe: Callable) -> tuple[str, bool, str | None]:
    pid = raw.get("process_pid")
    started = raw.get("process_creation_time")
    if pid is not None and (type(pid) is not int or not 0 < pid <= 0xFFFFFFFF):
        raise ValueError("invalid process_pid")
    if started is not None and (type(started) not in (int, float) or not math.isfinite(started) or started <= 0):
        raise ValueError("invalid process_creation_time")
    evidence = (pid is not None or started is not None or raw.get("status") in ("running", "cancelling")
                or raw.get("process_state") not in OFFLINE_PROCESS_STATES)
    identity = probe(pid, started) if evidence and pid is not None else ("unknown" if evidence else "none")
    if raw.get("status") in ("running", "cancelling"):
        return identity, evidence, "job is running or cancelling"
    if raw.get("process_state") not in OFFLINE_PROCESS_STATES or identity not in ("none", "absent"):
        return identity, evidence, "process state or identity is unsafe or unknown"
    if raw.get("transport_cleanup_state") not in OFFLINE_CLEANUP_STATES:
        return identity, evidence, "transport cleanup incomplete or unknown"
    return identity, evidence, None


def scan(root: Path, *, identity_probe: Callable | None = None) -> ImportPlan:
    from facebook_backend.persistence.state_startup import manifest_digest

    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root is not a directory")
    for name in ("api-jobs", "api-batches"):
        directory = root / name
        if directory.is_symlink() or directory.resolve() != directory or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"invalid source directory: {name}")
    digest = manifest_digest(root)
    jobs, batches, invalid_jobs, invalid_batches = [], [], [], []
    orphan_batches, duplicates, orphan_links, blocked = [], [], [], []
    keys = set()
    for path in sorted((root / "api-jobs").glob(JOB_GLOB)):
        try:
            raw = _load(path, root, "job_id", ".job.json")
            status = raw.get("status")
            if status not in ALLOWED_JOB_STATUSES:
                raise ValueError(f"unsupported status {status!r}")
            for key in ("attempts", "failover_count"):
                if raw.get(key) is not None and (type(raw[key]) is not int or raw[key] < 0):
                    raise ValueError(f"invalid {key}")
            if raw.get("attempt_dirs") is not None and not isinstance(raw["attempt_dirs"], list):
                raise ValueError("invalid attempt_dirs")
            key = raw.get("submission_key")
            if key is not None and (not isinstance(key, str) or not key.strip() or key != key.strip() or key in keys):
                raise ValueError("invalid or duplicate submission_key")
            if key is not None:
                keys.add(key)
            identity, evidence, reason = _offline_state(raw, identity_probe or process_manager.identity_state)
            artifacts, missing = _collect_artifacts(root, raw.get("run_dir"))
            entry = JobEntry(raw["job_id"], path, raw, status, identity, evidence,
                             reason is None and raw.get("recovery_state") != "failed-result-persistence",
                             reason, artifacts, missing)
            jobs.append(entry)
            if reason:
                blocked.append(dict(job_id=entry.job_id, status=status, identity=identity, reason=reason))
        except (OSError, ValueError, TypeError) as exc:
            invalid_jobs.append(dict(path=str(path), reason=str(exc)))

    seen = {job.job_id for job in jobs}
    membership = {}
    for path in sorted((root / "api-batches").glob(BATCH_GLOB)):
        try:
            raw = _load(path, root, "batch_id", ".batch.json")
            ids = raw.get("job_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("job_ids must be a nonempty list of job IDs")
            batch_id = raw["batch_id"]
            if raw.get("status") not in (None, "queued", "completed", "completed_with_errors"):
                raise ValueError("unsafe or unknown batch status")
            missing = [i for i in ids if i not in seen]
            if missing:
                orphan_batches.append(dict(batch_id=batch_id, path=str(path), missing_jobs=missing))
                continue
            for index, job_id in enumerate(ids, 1):
                if job_id in membership:
                    duplicates.append(dict(job_id=job_id, batch_id=batch_id, reason="repeated batch membership"))
                membership[job_id] = (batch_id, index)
            batches.append(BatchEntry(batch_id, path, raw, tuple(ids)))
        except (OSError, ValueError, TypeError) as exc:
            invalid_batches.append(dict(path=str(path), reason=str(exc)))

    statuses = {job.job_id: job.status for job in jobs}
    for batch in batches:
        declared = batch.manifest.get("status")
        if declared is not None and declared != _batch_counts(batch, statuses)["status"]:
            invalid_batches.append(dict(batch_id=batch.batch_id, path=str(batch.path),
                                        reason="batch status disagrees with member states"))
    for job in jobs:
        expected_batch, expected_index = membership.get(job.job_id, (None, None))
        declared = job.manifest.get("batch_id")
        index = job.manifest.get("batch_index")
        if ((declared is not None and declared != expected_batch) or
                (index is not None and (type(index) is not int or index != expected_index))):
            orphan_links.append(dict(job_id=job.job_id, batch_id=declared, batch_index=index,
                                     expected_batch=expected_batch, expected_index=expected_index))
    if manifest_digest(root) != digest:
        raise ValueError("source manifests changed during scan")
    return ImportPlan(root, digest, tuple(jobs), tuple(batches), tuple(invalid_jobs), tuple(invalid_batches),
                      tuple(orphan_batches), tuple(duplicates), tuple(orphan_links), tuple(blocked))


def _outside_sources(root: Path, target: Path) -> None:
    target = target.resolve()
    if any(target.is_relative_to(root / directory) for directory in ("api-jobs", "api-batches")):
        raise ValueError("destination overlaps source manifests")


def backup_manifests(root: Path, destination: Path | None = None) -> Path:
    """Reserve a fresh directory before copying. A failed copy blocks apply."""
    root = Path(root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = Path(destination) if destination else root / "backups" / f"manifest-import-{stamp}"
    _outside_sources(root, target)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("api-jobs", "api-batches"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, target / name, symlinks=True)
    return target


def _service_is_running(port: int = DEFAULT_API_PORT, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except ConnectionRefusedError:
        return False
    # Timeouts, permission errors, or other unknowns must stop the operation.


def _report(plan: ImportPlan, target: str, mode: str) -> dict:
    return dict(mode=mode, generated_at=utc_now(), root=str(plan.root), database=target,
                dry_run=mode == "dry-run", manifest_jobs=len(plan.jobs), manifest_batches=len(plan.batches),
                invalid_jobs=list(plan.invalid_jobs), invalid_batches=list(plan.invalid_batches),
                orphan_batches=list(plan.orphan_batches), duplicate_references=list(plan.duplicate_references),
                orphaned_job_batch_references=list(plan.orphaned_job_batch_references), blocked=list(plan.blocked),
                missing_required_artifacts={j.job_id: list(j.missing_required) for j in plan.jobs if j.missing_required},
                imported_jobs=0, imported_batches=0, imported_artifacts=0, imported_events=0,
                skipped_jobs=0, skipped_batches=0, missing_jobs=[], missing_batches=[], conflicts=[],
                backup_dir=None, forced_while_running=False, rolled_back=False, error=None, ok=False)


def _batch_counts(batch: BatchEntry, statuses: dict[str, str]) -> dict:
    counts = Counter(statuses[i] for i in batch.job_ids)
    total = len(batch.job_ids)
    status = "queued"
    if counts["completed"] == total:
        status = "completed"
    elif counts["completed"] + counts["failed"] + counts["cancelled"] == total:
        status = "completed_with_errors"
    return dict(status=status, total_jobs=total, completed_jobs=counts["completed"],
                failed_jobs=counts["failed"], cancelled_jobs=counts["cancelled"])


def _expected(database: Database, plan: ImportPlan) -> tuple[dict, dict]:
    # This is the runtime store's mapping, including structured errors, pinning,
    # last worker/serial, submission keys, process/transport and recovery data.
    store = DatabaseJobStore(database)
    jobs = {}
    statuses = {}
    for entry in plan.jobs:
        fields = store._job_columns(entry.manifest)
        fields.update(created_at=entry.manifest["created_at"], error_code=entry.manifest.get("error_code"))
        # SQLite stores the process birth time in a FLOAT column, even for a
        # whole-second timestamp represented as an integer in a v1 manifest.
        if fields["process_creation_time"] is not None:
            fields["process_creation_time"] = float(fields["process_creation_time"])
        jobs[entry.job_id] = fields
        statuses[entry.job_id] = entry.status
    batches = {}
    for batch in plan.batches:
        batches[batch.batch_id] = dict(created_at=batch.manifest["created_at"],
                                       run_dir=batch.manifest.get("run_dir"),
                                       completion_scope=batch.manifest.get("completion_scope"),
                                       **_batch_counts(batch, statuses))
        for index, job_id in enumerate(batch.job_ids, 1):
            jobs[job_id].update(batch_id=batch.batch_id, batch_index=index)
    return jobs, batches


def _different(actual: dict, expected: dict) -> list[str]:
    # JSON equality distinguishes booleans from numbers in nested payloads.
    return [field for field, value in expected.items() if
            json.dumps(actual.get(field), sort_keys=True) != json.dumps(value, sort_keys=True)]


class ImportConflict(ValueError):
    """Leave the unit of work through its rollback branch."""


def _compare(session: Session, plan: ImportPlan, expected: tuple[dict, dict], report: dict) -> None:
    jobs, batches = expected
    jobs_repo, batches_repo = JobRepository(session), BatchRepository(session)
    artifact_repo = ArtifactRepository(session)
    report.update(missing_jobs=[], missing_batches=[], conflicts=[])
    for job in plan.jobs:
        ident = job.job_id
        row = jobs_repo.get(ident)
        if row is None:
            report["missing_jobs"].append(ident)
            continue
        fields = jobs[ident]
        # The runtime repository deliberately tolerates invalid historical JSON.
        # Migration must distinguish corrupt JSON from {}, null or a missing row.
        encoded_payload, encoded_result = session.execute(
            select(Job.payload, Job.result).where(Job.id == ident)
        ).one()
        corrupt = []
        for key, encoded in (("payload", encoded_payload), ("result", encoded_result)):
            try:
                row[key] = json.loads(encoded) if encoded is not None else None
            except (TypeError, ValueError):
                corrupt.append(key)
        differences = corrupt + _different(row, fields)
        links = list(session.execute(select(BatchJob.batch_id, BatchJob.position).where(BatchJob.job_id == ident)))
        if links != ([(fields["batch_id"], fields["batch_index"])] if fields["batch_id"] else []):
            differences.append("batch_jobs")
        artifacts = {a["artifact_name"]: a for a in artifact_repo.list_for_job(ident)}
        for artifact in job.artifacts:
            actual = artifacts.pop(artifact.name, {})
            differences.extend(f"artifacts.{artifact.name}.{f}" for f in _different(actual, artifact.fields()))
        differences.extend(f"artifacts.{name}.unexpected" for name in artifacts)
        if differences:
            report["conflicts"].append(dict(job_id=ident, fields=differences))
    for batch in plan.batches:
        ident = batch.batch_id
        row = batches_repo.get(ident)
        if row is None:
            report["missing_batches"].append(ident)
            continue
        differences = _different(row, batches[ident])
        if batches_repo.job_ids(ident) != list(batch.job_ids):
            differences.append("job_ids")
        if [j["id"] for j in jobs_repo.for_batch(ident)] != list(batch.job_ids):
            differences.append("jobs.batch_index")
        if differences:
            report["conflicts"].append(dict(batch_id=ident, fields=differences))

    target_keys = {j.manifest["submission_key"]: j.job_id for j in plan.jobs if j.manifest.get("submission_key")}
    if target_keys:
        for ident, payload in session.execute(select(Job.id, Job.payload)):
            key = json.loads(payload).get("submission_key")
            if key in target_keys and target_keys[key] != ident:
                report["conflicts"].append(dict(job_id=ident, fields=["payload.submission_key"]))
    report["counts_match"] = not (report["missing_jobs"] or report["missing_batches"])


def _write_missing(session: Session, plan: ImportPlan, expected: tuple[dict, dict], report: dict) -> None:
    jobs, batches = expected
    missing_jobs, missing_batches = set(report["missing_jobs"]), set(report["missing_batches"])
    job_repo, batch_repo = JobRepository(session), BatchRepository(session)
    events, artifacts = EventRepository(session), ArtifactRepository(session)
    for batch in plan.batches:
        if batch.batch_id in missing_batches:
            fields = batches[batch.batch_id]
            batch_repo.create(batch_id=batch.batch_id, **{k: fields[k] for k in
                              ("created_at", "status", "run_dir", "total_jobs", "completion_scope")})
            events.append(event_type="legacy-import", batch_id=batch.batch_id,
                          payload={"source": str(batch.path), "job_ids": list(batch.job_ids)})
    for entry in plan.jobs:
        if entry.job_id in missing_jobs:
            job_repo.create(job_id=entry.job_id, **jobs[entry.job_id])
            for artifact in entry.artifacts:
                artifacts.upsert(job_id=entry.job_id, **artifact.fields())
                report["imported_artifacts"] += 1
            events.append(event_type="legacy-import", job_id=entry.job_id,
                          payload=dict(source=str(entry.path), status=entry.status,
                                       process_identity=entry.identity, safe_to_resume=entry.safe_to_resume))
    for batch in plan.batches:
        if batch.batch_id in missing_batches:
            batch_repo.link_jobs(batch.batch_id, batch.job_ids)
    report.update(imported_jobs=len(missing_jobs), imported_batches=len(missing_batches),
                  skipped_jobs=len(jobs)-len(missing_jobs), skipped_batches=len(batches)-len(missing_batches),
                  imported_events=len(missing_jobs)+len(missing_batches))


def _check_idle_target(session: Session) -> None:
    active = session.scalar(select(DeviceLease.id).where(DeviceLease.released_at.is_(None)).limit(1))
    if active is not None:
        raise ImportConflict("target database has an unreleased device lease")
    unsafe = session.scalar(select(Job.id).where(or_(
        Job.status.not_in(("queued", "completed", "failed", "cancelled")),
        Job.process_state.not_in(OFFLINE_PROCESS_STATES[1:]),
        Job.transport_cleanup_state.not_in(OFFLINE_CLEANUP_STATES[1:]),
    )).limit(1))
    if unsafe is not None:
        raise ImportConflict(f"target job {unsafe} has an unsafe or unknown state")
    identities = session.execute(select(Job.id, Job.process_pid, Job.process_creation_time).where(
        or_(Job.process_pid.is_not(None), Job.process_creation_time.is_not(None))))
    for job_id, pid, creation in identities:
        if pid is None or process_manager.identity_state(pid, creation) != "absent":
            raise ImportConflict(f"target job {job_id} has a live or unverified process identity")


def _run_locked(database: Database, plan: ImportPlan, report: dict, *, backup: bool,
                backup_dir: Path | None, service_probe: Callable) -> dict:
    from facebook_backend.persistence.state_startup import manifest_digest, write_cutover_receipt

    apply = report["mode"] == "apply"
    if not plan.ok:
        report["error"] = "source validation failed"
        return report
    if service_probe() is not False:
        report["error"] = "the capture API is running or its state is unknown; stop it before importing"
        return report
    if manifest_digest(plan.root) != plan.digest:
        report["error"] = "source manifests changed after scan"
        return report
    if apply and backup:
        try:
            dest = backup_manifests(plan.root, backup_dir)
            report["backup_dir"] = str(dest)
            if manifest_digest(dest) != plan.digest:
                raise ValueError("backup differs from source manifests")
        except (OSError, ValueError) as exc:
            report["error"] = f"backup failed: {exc}"
            return report
    expected = _expected(database, plan)
    try:
        with database.session() as session:
            session.execute(text("BEGIN IMMEDIATE" if apply else "BEGIN"))
            if apply:
                _check_idle_target(session)
            _compare(session, plan, expected, report)
            if report["conflicts"]:
                raise ImportConflict("target differs from source")
            if apply:
                _write_missing(session, plan, expected, report)
                _compare(session, plan, expected, report)
            if report["conflicts"] or not report["counts_match"]:
                raise ImportConflict("target reconciliation failed")
            if manifest_digest(plan.root) != plan.digest:
                raise ImportConflict("source manifests changed during reconciliation")
            if apply:
                write_cutover_receipt(session, plan.root, plan.digest)
            report.update(database_jobs=JobRepository(session).count(), database_batches=BatchRepository(session).count(),
                          database_artifacts=ArtifactRepository(session).count())
    except ImportConflict as exc:
        report.update(rolled_back=apply, error=str(exc), imported_jobs=0, imported_batches=0,
                      imported_artifacts=0, imported_events=0, skipped_jobs=0, skipped_batches=0)
        return report
    report["ok"] = True
    return report


def apply_plan(database: Database, plan: ImportPlan, *, dry_run: bool = True, backup: bool = True,
               backup_dir: Path | None = None, force: bool = False, service_probe: Callable | None = None,
               verify: bool = False) -> dict:
    """Unit-of-work API for an already constructed database and source plan."""
    from facebook_backend.persistence.state_startup import state_owner

    mode = "verify" if verify else ("dry-run" if dry_run else "apply")
    report = _report(plan, database.database_path(), mode)
    if force:
        report["error"] = "--force is unsupported; stop services and resolve conflicts first"
    elif mode == "dry-run":
        report["ok"] = plan.ok
    else:
        with state_owner(plan.root, str(database.engine.url)):
            _run_locked(database, plan, report, backup=backup, backup_dir=backup_dir,
                        service_probe=service_probe or _service_is_running)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=".", help="source root containing api-jobs and api-batches")
    parser.add_argument("--database-url", help="SQLite file URL; overrides FACEBOOK_DB_URL, then ROOT/data/facebook_backend.db")
    modes = parser.add_mutually_exclusive_group()
    for mode, help_text in (("dry-run", "validate source only (default)"), ("verify", "compare source with target, read-only"),
                            ("apply", "reconcile and import in one transaction"), ("rollback", "export database into a new independent directory")):
        modes.add_argument("--"+mode, action="store_const", dest="mode", const=mode, help=help_text)
    parser.set_defaults(mode="dry-run")
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument("--backup", nargs="?", const="", metavar="NEW_DIR", help="back up before apply (default); optionally name a new directory")
    backup.add_argument("--backup-dir", dest="backup", metavar="NEW_DIR", help="alias for --backup NEW_DIR")
    backup.add_argument("--no-backup", action="store_true", help="explicitly skip source manifest backup for apply")
    parser.add_argument("--rollback-dir", metavar="NEW_DIR", help="fresh destination required for --rollback")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT, help="local API port used to guard offline operations")
    parser.add_argument("--report", help="write JSON to a new file; existing files are never overwritten")
    return parser


def _emit(value: str, *, stream: Any = None) -> None:
    target = stream if stream is not None else sys.stdout
    try:
        print(value, file=target)
    except UnicodeEncodeError:
        print(value.encode("ascii", "replace").decode("ascii"), file=target)


def render_report(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def _execute(args, root: Path, url: str, target: Path) -> dict:
    if args.mode == "dry-run":
        plan = scan(root)
        report = _report(plan, str(target), args.mode)
        report["ok"] = plan.ok
        return report
    if args.mode == "rollback":
        from facebook_backend.persistence.manifest_export import rollback_from_sqlite
        if _service_is_running(args.api_port) is not False:
            raise ValueError("stop the capture API before rollback")
        return rollback_from_sqlite(root, Path(args.rollback_dir), database_url=url)

    from facebook_backend.persistence.state_startup import state_owner, validate_schema
    with state_owner(root, url):
        if _service_is_running(args.api_port) is not False:
            raise ValueError("the capture API is running or unknown; stop it before importing")
        if not target.is_file():
            raise ValueError("target database is missing; run alembic upgrade head first")
        if args.mode == "verify":
            engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(target.as_uri()+"?mode=ro", uri=True))
            database = Database(engine)
        else:
            database = Database.open(url)
        with database:
            validate_schema(database)
            plan = scan(root)
            report = _report(plan, str(target), args.mode)
            return _run_locked(database, plan, report, backup=not args.no_backup,
                               backup_dir=Path(args.backup) if args.backup else None,
                               service_probe=lambda: _service_is_running(args.api_port))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.backup is not None or args.no_backup) and args.mode != "apply":
        parser.error("backup options require --apply")
    if bool(args.rollback_dir) != (args.mode == "rollback"):
        parser.error("--rollback and --rollback-dir NEW_DIR must be specified together")
    if not 1 <= args.api_port <= 65535:
        parser.error("--api-port must be between 1 and 65535")
    root = Path(args.root).resolve()
    url = args.database_url or os.getenv("FACEBOOK_DB_URL") or default_database_url(root)
    output = None
    try:
        parsed = make_url(url)
        if (parsed.drivername not in ("sqlite", "sqlite+pysqlite") or not parsed.database or
                parsed.database == ":memory:" or parsed.database.startswith("file:") or parsed.query):
            raise ValueError("offline import needs a SQLite file URL")
        target = Path(parsed.database).resolve()
        _outside_sources(root, target)
        if not root.is_dir():
            raise ValueError("source root does not exist")
        if args.report:
            path = Path(args.report).resolve()
            _outside_sources(root, path)
            if path in (target, *(Path(str(target)+suffix) for suffix in ("-wal", "-shm", "-journal"))):
                raise ValueError("report overlaps the target database")
            output = path.open("x", encoding="utf-8")
        try:
            report = _execute(args, root, url, target)
        except Exception as exc:  # CLI boundary; the database session already rolled back
            report = dict(mode=args.mode, ok=False, error=f"{type(exc).__name__}: {exc}")
        rendered = render_report(report)
        if output is not None:
            output.write(rendered + "\n")
        _emit(rendered)
        return 0 if report["ok"] else 2
    except Exception as exc:
        _emit(f"{type(exc).__name__}: {exc}", stream=sys.stderr)
        return 1
    finally:
        if output is not None:
            output.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
