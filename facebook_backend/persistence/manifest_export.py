"""SQLite-to-manifest compatibility formatter and explicit offline rollback entry.

For an operator rollback, use ``legacy_manifest_import --rollback --rollback-dir NEW_DIR``.
The legacy per-file formatter remains available for callers writing test fixtures.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from facebook_backend.config import PROJECT_ROOT
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.job_store import DatabaseJobStore, ManifestJobStore, batch_payload
from facebook_backend.persistence.offline_rollback import checked_id, rollback_from_sqlite
from facebook_backend.persistence.repositories import BatchRepository, JobRepository


@dataclass
class ExportReport:
    jobs_written: int = 0
    jobs_skipped: int = 0
    batches_written: int = 0
    batches_skipped: int = 0
    skipped_jobs: list[str] = field(default_factory=list)
    skipped_batches: list[str] = field(default_factory=list)
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "jobs_written": self.jobs_written,
            "jobs_skipped": self.jobs_skipped,
            "batches_written": self.batches_written,
            "batches_skipped": self.batches_skipped,
            "skipped_jobs": self.skipped_jobs,
            "skipped_batches": self.skipped_batches,
        }


def export_to_manifests(
    database: Database,
    root: Path,
    *,
    apply: bool = False,
    overwrite: bool = False,
) -> ExportReport:
    source = DatabaseJobStore(database)
    target = ManifestJobStore(root)
    report = ExportReport(dry_run=not apply)

    if overwrite:
        raise ValueError("in-place manifest replacement is disabled; use an independent rollback directory")
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        source._validate_batch_links(session)
        jobs = [(source._job_from_row(row), checked_id(row['id'], 'fb'))
                for row in JobRepository(session).list_all()]
        repo = BatchRepository(session)
        batches = [(batch_payload(dict(row, batch_id=row['id'], job_ids=repo.job_ids(row['id']))),
                    checked_id(row['id'], 'batch')) for row in repo.list_all()]

    for raw, job_id in jobs:
        path = target.job_path(job_id)
        if path.exists() and not overwrite:
            report.jobs_skipped += 1
            report.skipped_jobs.append(job_id)
            continue
        if apply:
            target.save_job(raw)
        report.jobs_written += 1

    for raw, batch_id in batches:
        path = target.batch_path(batch_id)
        if path.exists() and not overwrite:
            report.batches_skipped += 1
            report.skipped_batches.append(batch_id)
            continue
        if apply:
            target.save_batch(raw)
        report.batches_written += 1

    return report


def main(argv: Sequence[str] | None = None) -> int:
    # Keep the historical command discoverable, but route every operator write
    # through exactly the same offline checks as the migration command.
    from .legacy_manifest_import import main as migration_main

    parser = argparse.ArgumentParser(description="Export a checked SQLite rollback bundle into a new directory")
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    parser.add_argument("--database-url")
    parser.add_argument("--rollback-dir", help="new directory for the SQLite backup and manifests")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.dry_run:
        if not args.rollback_dir:
            parser.error("--apply requires --rollback-dir NEW_DIRECTORY (in-place rollback is disabled)")
        flags = ["--root", args.root, "--rollback", "--rollback-dir", args.rollback_dir]
        if args.database_url:
            flags += ["--database-url", args.database_url]
        return migration_main(flags)
    # Preserve the preview without modifying source files or publishing a target.
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = rollback_from_sqlite(Path(args.root), Path(tmp) / "preview", database_url=args.database_url)
        for key in ("destination", "backup"):
            result.pop(key)
        result["dry_run"] = True
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
