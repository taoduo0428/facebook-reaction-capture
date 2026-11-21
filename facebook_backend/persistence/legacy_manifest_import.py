"""Offline import of the legacy JSON manifests into SQLite.

Usage (dry-run is the default and writes nothing):

```
python -m facebook_backend.persistence.legacy_manifest_import --root . --dry-run
python -m facebook_backend.persistence.legacy_manifest_import --root . --apply
```

Guarantees, each covered by ``tests/test_backend_persistence.py``:

* **Service stopped** — ``apply`` refuses while the API answers on its port
  (``--force`` exists for a deliberate override and says so in the report).
* **Backup first** — the manifests are copied to
  ``backups/manifest-import-<timestamp>/`` before the first write; the
  originals are never deleted or rewritten by this tool.
* **One transaction** — every row is written inside a single
  ``Database.session()``; any failure rolls the whole import back.
* **Idempotent** — re-running skips records that already exist with identical
  content and reports conflicts instead of overwriting them.
* **Reconciled** — job/batch counts, artifact paths and process identities are
  checked and reported; ``ok`` is false when anything does not add up.
* **No silent recovery claim** — a job whose process identity is ``match`` or
  ``unknown`` keeps its status and is reported as blocked; the import never
  marks anything "safe to resume".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from facebook_backend.artifacts.catalog import ARTIFACT_SPECS, REQUIRED_ARTIFACTS
from facebook_backend.artifacts.manager import checked_artifact_path
from facebook_backend.domain.exceptions import ApiError
from facebook_backend.persistence.database import Database, default_database_url
from facebook_backend.persistence.models import utc_now
from facebook_backend.persistence.repositories import (
    ArtifactRepository,
    BatchRepository,
    EventRepository,
    JobRepository,
)
from facebook_backend.platform import process_manager

JOB_GLOB = "fb_*.job.json"
BATCH_GLOB = "batch_*.batch.json"
ALLOWED_JOB_STATUSES = frozenset(
    {"queued", "running", "cancelling", "completed", "failed", "cancelled"}
)
UNVERIFIED_PROCESS_STATES = frozenset(
    {"running", "stop-unverified", "restart-process-still-running", "restart-unverified"}
)
DEFAULT_API_PORT = 8765
CHUNK = 1024 * 1024


# --------------------------------------------------------------------------
# plan model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ArtifactEntry:
    name: str
    path: str
    size: int
    sha256: str
    content_type: str


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
    jobs: tuple[JobEntry, ...] = ()
    batches: tuple[BatchEntry, ...] = ()
    invalid_jobs: tuple[dict[str, Any], ...] = ()
    invalid_batches: tuple[dict[str, Any], ...] = ()
    orphan_batches: tuple[dict[str, Any], ...] = ()
    duplicate_references: tuple[dict[str, Any], ...] = ()
    blocked: tuple[dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        """False when the manifests cannot be fully represented in the database.

        Orphan batches count as not-ok on purpose: the legacy loader silently
        skipped them, and a migration must not lose a batch without the operator
        seeing it.  ``--apply`` still imports everything it can; the non-zero
        exit code is a signal, not a refusal.
        """
        return not (
            self.invalid_jobs
            or self.invalid_batches
            or self.duplicate_references
            or self.orphan_batches
        )


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_artifacts(run_dir: Path | None) -> tuple[tuple[ArtifactEntry, ...], tuple[str, ...]]:
    """Existing, confined artifacts plus the names of missing required ones."""
    if run_dir is None or not run_dir.is_dir():
        return (), tuple(sorted(REQUIRED_ARTIFACTS))
    found: list[ArtifactEntry] = []
    present: set[str] = set()
    for name, _label, _layer, relative in ARTIFACT_SPECS:
        try:
            path = checked_artifact_path(run_dir, Path(relative))
            size = path.stat().st_size
            digest = _sha256(path)
        except (ApiError, OSError):
            continue
        present.add(name)
        found.append(
            ArtifactEntry(
                name=name,
                path=str(path),
                size=size,
                sha256=digest,
                content_type="text/csv; charset=utf-8" if path.suffix.lower() == ".csv" else "",
            )
        )
    missing = tuple(sorted(REQUIRED_ARTIFACTS - present))
    return tuple(found), missing


def _resolve_run_dir(root: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def _job_entry(
    root: Path,
    path: Path,
    manifest: dict[str, Any],
    identity_probe: Callable[[int | None, float | None], str],
) -> JobEntry:
    status = str(manifest.get("status") or "").strip()
    process_pid = process_manager.coerce_pid(manifest.get("process_pid"))
    creation_time = process_manager.coerce_creation_time(manifest.get("process_creation_time"))
    process_state = str(manifest.get("process_state") or "") or None
    process_evidence = bool(
        (status in {"running", "cancelling"} and process_state != "stopped")
        or process_pid is not None
        or process_state in UNVERIFIED_PROCESS_STATES
    )
    identity = identity_probe(process_pid, creation_time) if process_evidence else "none"
    safe = not process_evidence or identity == "absent"
    reason: str | None = None
    if process_evidence:
        if identity == "match":
            reason = "persisted capture process is still running"
        elif identity == "unknown":
            reason = "persisted capture process identity could not be verified"
        elif identity == "mismatch":
            reason = "persisted pid belongs to a different process"
    run_dir = _resolve_run_dir(root, manifest.get("run_dir"))
    artifacts, missing = _collect_artifacts(run_dir)
    return JobEntry(
        job_id=str(manifest.get("job_id")),
        path=path,
        manifest=manifest,
        status=status,
        identity=identity,
        process_evidence=process_evidence,
        safe_to_resume=safe,
        blocked_reason=reason,
        artifacts=artifacts,
        missing_required=missing,
    )


def scan(
    root: Path,
    *,
    identity_probe: Callable[[int | None, float | None], str] | None = None,
) -> ImportPlan:
    """Read both manifest directories without touching the database."""
    root = Path(root)
    probe = identity_probe or process_manager.identity_state
    jobs: list[JobEntry] = []
    invalid_jobs: list[dict[str, Any]] = []
    seen_jobs: dict[str, Path] = {}

    job_dir = root / "api-jobs"
    if job_dir.is_dir():
        for path in sorted(job_dir.glob(JOB_GLOB)):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                invalid_jobs.append({"path": str(path), "reason": f"unreadable manifest: {exc}"})
                continue
            if not isinstance(raw, dict) or raw.get("version") != 1:
                invalid_jobs.append({"path": str(path), "reason": "manifest version is not 1"})
                continue
            job_id = str(raw.get("job_id") or "").strip()
            expected = path.name.removesuffix(".job.json")
            if not job_id or job_id != expected:
                invalid_jobs.append({"path": str(path), "reason": "job_id does not match the file name"})
                continue
            status = str(raw.get("status") or "").strip()
            if status not in ALLOWED_JOB_STATUSES:
                invalid_jobs.append({"path": str(path), "reason": f"unsupported status {status!r}"})
                continue
            if job_id in seen_jobs:
                invalid_jobs.append(
                    {"path": str(path), "reason": f"duplicate job_id also present at {seen_jobs[job_id]}"}
                )
                continue
            seen_jobs[job_id] = path
            jobs.append(_job_entry(root, path, raw, probe))

    batches: list[BatchEntry] = []
    invalid_batches: list[dict[str, Any]] = []
    orphan_batches: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    batch_dir = root / "api-batches"
    if batch_dir.is_dir():
        for path in sorted(batch_dir.glob(BATCH_GLOB)):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                invalid_batches.append({"path": str(path), "reason": f"unreadable manifest: {exc}"})
                continue
            if not isinstance(raw, dict) or raw.get("version") != 1:
                invalid_batches.append({"path": str(path), "reason": "manifest version is not 1"})
                continue
            batch_id = str(raw.get("batch_id") or "").strip()
            if not batch_id or path.name != f"{batch_id}.batch.json":
                invalid_batches.append(
                    {"path": str(path), "reason": "batch_id does not match the file name"}
                )
                continue
            job_ids = [str(value) for value in raw.get("job_ids") or []]
            if not job_ids:
                invalid_batches.append({"path": str(path), "reason": "batch has no jobs"})
                continue
            if len(set(job_ids)) != len(job_ids):
                duplicates.append({"batch_id": batch_id, "path": str(path), "reason": "duplicate job_id in batch"})
            missing = [job_id for job_id in job_ids if job_id not in seen_jobs]
            if missing:
                orphan_batches.append(
                    {"batch_id": batch_id, "path": str(path), "missing_jobs": missing}
                )
                continue
            batches.append(BatchEntry(batch_id=batch_id, path=path, manifest=raw, job_ids=tuple(job_ids)))

    blocked = [
        {
            "job_id": job.job_id,
            "status": job.status,
            "identity": job.identity,
            "reason": job.blocked_reason,
        }
        for job in jobs
        if not job.safe_to_resume
    ]
    return ImportPlan(
        root=root,
        jobs=tuple(jobs),
        batches=tuple(batches),
        invalid_jobs=tuple(invalid_jobs),
        invalid_batches=tuple(invalid_batches),
        orphan_batches=tuple(orphan_batches),
        duplicate_references=tuple(duplicates),
        blocked=tuple(blocked),
    )


# --------------------------------------------------------------------------
# backup
# --------------------------------------------------------------------------
def backup_manifests(root: Path, destination: Path | None = None) -> Path:
    """Copy the manifest directories (never move or delete the originals)."""
    root = Path(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = Path(destination) if destination else root / "backups" / f"manifest-import-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("api-jobs", "api-batches"):
        source = root / name
        if not source.is_dir():
            continue
        shutil.copytree(source, target / name, dirs_exist_ok=True)
    return target


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def _service_is_running(port: int = DEFAULT_API_PORT, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _job_payload(entry: JobEntry) -> dict[str, Any]:
    manifest = entry.manifest
    return {
        "request": manifest.get("request") or {},
        "attempt_dirs": list(manifest.get("attempt_dirs") or []),
        "checkpoint": manifest.get("checkpoint"),
        "legacy_manifest": str(entry.path),
        "legacy_batch_id": manifest.get("batch_id"),
    }


def _job_fields(
    entry: JobEntry,
    planned_batch_ids: set[str],
    job_batch: dict[str, tuple[str, int]],
) -> dict[str, Any]:
    manifest = entry.manifest
    recovery_state = manifest.get("recovery_state")
    if not entry.safe_to_resume:
        recovery_state = "import-blocked-process-" + (entry.identity or "unknown")
    derived_batch, derived_index = job_batch.get(entry.job_id, (None, None))
    batch_id = manifest.get("batch_id")
    if batch_id is None:
        batch_id = derived_batch
    batch_index = manifest.get("batch_index")
    if batch_index is None:
        batch_index = derived_index
    if batch_id not in planned_batch_ids:
        # The job referenced a batch whose manifest is missing/invalid.  The FK
        # cannot point at a non-existent batch, so the relation is dropped here
        # and kept in the payload for traceability; the report lists it.
        batch_id = None
    return {
        "status": entry.status,
        "created_at": manifest.get("created_at") or utc_now(),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "cancel_requested_at": manifest.get("cancel_requested_at"),
        "payload": _job_payload(entry),
        "result": manifest.get("result"),
        "error_message": (
            json.dumps(manifest.get("error"), ensure_ascii=False)
            if manifest.get("error") is not None
            else None
        ),
        "assigned_worker_id": manifest.get("worker_id"),
        "device_serial": manifest.get("serial"),
        "attempt_count": int(manifest.get("attempts") or 0),
        "failover_count": int(manifest.get("failover_count") or 0),
        "batch_id": batch_id,
        "batch_index": batch_index,
        "completion_scope": manifest.get("completion_scope"),
        "run_dir": manifest.get("run_dir"),
        "capture_run_dir": manifest.get("capture_run_dir"),
        "process_pid": process_manager.coerce_pid(manifest.get("process_pid")),
        "process_creation_time": process_manager.coerce_creation_time(
            manifest.get("process_creation_time")
        ),
        "process_state": manifest.get("process_state"),
        "transport_cleanup_state": manifest.get("transport_cleanup_state"),
        "recovery_state": recovery_state,
    }


def _same_existing(existing: dict[str, Any], fields: dict[str, Any]) -> bool:
    for key in ("status", "run_dir", "batch_id", "process_pid", "process_state"):
        if existing.get(key) != fields.get(key):
            return False
    return True


def apply_plan(
    database: Database,
    plan: ImportPlan,
    *,
    dry_run: bool = True,
    backup: bool = True,
    backup_dir: Path | None = None,
    force: bool = False,
    service_probe: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Write the plan into the database (or only report what would happen)."""
    probe = service_probe or _service_is_running
    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "dry_run": dry_run,
        "root": str(plan.root),
        "database": database.database_path(),
        "manifest_jobs": len(plan.jobs),
        "manifest_batches": len(plan.batches),
        "invalid_jobs": list(plan.invalid_jobs),
        "invalid_batches": list(plan.invalid_batches),
        "orphan_batches": list(plan.orphan_batches),
        "duplicate_references": list(plan.duplicate_references),
        "orphaned_job_batch_references": [],
        "blocked": list(plan.blocked),
        "imported_jobs": 0,
        "skipped_jobs": 0,
        "conflicts": [],
        "imported_batches": 0,
        "skipped_batches": 0,
        "imported_artifacts": 0,
        "imported_events": 0,
        "backup_dir": None,
        "forced_while_running": False,
        "rolled_back": False,
        "error": None,
        "ok": False,
    }

    if not dry_run and probe():
        if not force:
            report["error"] = (
                "the capture API appears to be running; stop it before importing "
                "(or pass --force and accept the risk)"
            )
            return report
        report["forced_while_running"] = True

    if dry_run:
        report["imported_jobs"] = len(plan.jobs)
        report["imported_batches"] = len(plan.batches)
        report["imported_artifacts"] = sum(len(job.artifacts) for job in plan.jobs)
        report["ok"] = plan.ok
        return report

    if backup:
        report["backup_dir"] = str(backup_manifests(plan.root, backup_dir))

    try:
        with database.session() as session:
            jobs_repo = JobRepository(session)
            batches_repo = BatchRepository(session)
            artifacts_repo = ArtifactRepository(session)
            events_repo = EventRepository(session)
            pre_jobs = jobs_repo.count()
            pre_batches = batches_repo.count()
            planned_batch_ids = {entry.batch_id for entry in plan.batches}
            job_batch = {
                job_id: (entry.batch_id, position)
                for entry in plan.batches
                for position, job_id in enumerate(entry.job_ids, start=1)
            }

            # Batches first: ``jobs.batch_id`` is a foreign key, so the parent
            # row has to exist before any child is inserted.
            for entry in plan.batches:
                if batches_repo.get(entry.batch_id) is not None:
                    report["skipped_batches"] += 1
                    continue
                batches_repo.create(
                    batch_id=entry.batch_id,
                    status=str(entry.manifest.get("status") or "queued"),
                    created_at=entry.manifest.get("created_at") or utc_now(),
                    run_dir=entry.manifest.get("run_dir"),
                    completion_scope=entry.manifest.get("completion_scope"),
                    total_jobs=len(entry.job_ids),
                )
                report["imported_batches"] += 1
                events_repo.append(
                    event_type="legacy-import",
                    batch_id=entry.batch_id,
                    payload={"source": str(entry.path), "job_ids": list(entry.job_ids)},
                )
                report["imported_events"] += 1

            for entry in plan.jobs:
                fields = _job_fields(entry, planned_batch_ids, job_batch)
                if entry.manifest.get("batch_id") and fields["batch_id"] is None:
                    report["orphaned_job_batch_references"].append(
                        {"job_id": entry.job_id, "batch_id": entry.manifest.get("batch_id")}
                    )
                existing = jobs_repo.get(entry.job_id)
                if existing is not None:
                    if _same_existing(existing, fields):
                        report["skipped_jobs"] += 1
                        continue
                    report["conflicts"].append(
                        {"job_id": entry.job_id, "reason": "already present with different content"}
                    )
                    continue
                jobs_repo.create(
                    job_id=entry.job_id,
                    payload=fields.pop("payload"),
                    created_at=fields.pop("created_at"),
                    **fields,
                )
                report["imported_jobs"] += 1
                for artifact in entry.artifacts:
                    artifacts_repo.upsert(
                        job_id=entry.job_id,
                        artifact_name=artifact.name,
                        artifact_path=artifact.path,
                        size=artifact.size,
                        sha256=artifact.sha256,
                        content_type=artifact.content_type or None,
                        verification_status="verified",
                    )
                    report["imported_artifacts"] += 1
                events_repo.append(
                    event_type="legacy-import",
                    job_id=entry.job_id,
                    payload={
                        "source": str(entry.path),
                        "status": entry.status,
                        "process_identity": entry.identity,
                        "safe_to_resume": entry.safe_to_resume,
                    },
                )
                report["imported_events"] += 1

            for entry in plan.batches:
                batches_repo.link_jobs(entry.batch_id, list(entry.job_ids))
                jobs_repo.backfill_batch_relation(entry.batch_id, list(entry.job_ids))

            # Reconciliation inside the same transaction.
            report["database_jobs"] = jobs_repo.count()
            report["database_batches"] = batches_repo.count()
            report["database_artifacts"] = artifacts_repo.count()
            report["missing_jobs"] = [
                entry.job_id for entry in plan.jobs if jobs_repo.get(entry.job_id) is None
            ]
            report["missing_batches"] = [
                entry.batch_id for entry in plan.batches if batches_repo.get(entry.batch_id) is None
            ]
    except Exception as exc:  # noqa: BLE001 - reported, then re-raised for the CLI
        report["rolled_back"] = True
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise

    report["counts_match"] = bool(
        not report["missing_jobs"]
        and not report["missing_batches"]
        and report["database_jobs"] == pre_jobs + report["imported_jobs"]
        and report["database_batches"] == pre_batches + report["imported_batches"]
    )
    report["ok"] = bool(
        plan.ok
        and not report["conflicts"]
        and report["counts_match"]
        and report["error"] is None
    )
    return report


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "legacy manifest import",
        f"  mode: {'dry-run' if report['dry_run'] else 'apply'}",
        f"  root: {report['root']}",
        f"  database: {report['database']}",
        f"  manifest jobs: {report['manifest_jobs']}  batches: {report['manifest_batches']}",
        f"  imported jobs: {report['imported_jobs']}  skipped: {report['skipped_jobs']}  "
        f"conflicts: {len(report['conflicts'])}",
        f"  imported batches: {report['imported_batches']}  skipped: {report['skipped_batches']}",
        f"  artifacts: {report['imported_artifacts']}  events: {report['imported_events']}",
        f"  blocked jobs (not safe to resume): {len(report['blocked'])}",
        f"  invalid job manifests: {len(report['invalid_jobs'])}  "
        f"invalid batch manifests: {len(report['invalid_batches'])}  "
        f"orphan batches: {len(report['orphan_batches'])}  "
        f"dropped job→batch links: {len(report['orphaned_job_batch_references'])}",
    ]
    if report.get("backup_dir"):
        lines.append(f"  backup: {report['backup_dir']}")
    if report.get("forced_while_running"):
        lines.append("  WARNING: forced while the API was answering")
    if report.get("error"):
        lines.append(f"  error: {report['error']}")
    if report.get("rolled_back"):
        lines.append("  transaction rolled back; the database is unchanged")
    if "counts_match" in report:
        lines.append(f"  counts match: {report['counts_match']}")
    lines.append(f"  result: {'OK' if report['ok'] else 'ATTENTION REQUIRED'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import legacy JSON manifests into SQLite")
    parser.add_argument("--root", default=".", help="project root containing api-jobs/ and api-batches/")
    parser.add_argument("--database-url", default=None, help="SQLAlchemy URL (default: data/facebook_backend.db)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="report only (default when --apply is absent)")
    parser.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument("--no-backup", action="store_true", help="skip the manifest backup (not recommended)")
    parser.add_argument("--backup-dir", default=None, help="explicit backup directory")
    parser.add_argument("--report", default=None, help="write the JSON report to this path")
    parser.add_argument("--force", action="store_true", help="apply even if the API still answers")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    database = Database.open(args.database_url or default_database_url(root))
    plan = scan(root)
    dry_run = not args.apply
    try:
        report = apply_plan(
            database,
            plan,
            dry_run=dry_run,
            backup=not args.no_backup,
            backup_dir=Path(args.backup_dir) if args.backup_dir else None,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        database.dispose()

    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(render_report(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main())
