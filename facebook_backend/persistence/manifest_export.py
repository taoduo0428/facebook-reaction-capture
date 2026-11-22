"""Export the database state back to JSON manifests.

The reverse of :mod:`legacy_manifest_import`, and the reason a switch to
``FACEBOOK_JOB_STORE=database`` stays reversible: jobs created while the database
was authoritative have no manifest, so rolling back to the manifest store would
lose them without this step.

    python -m facebook_backend.persistence.manifest_export --dry-run
    python -m facebook_backend.persistence.manifest_export --apply

Existing manifest files are never overwritten unless ``--overwrite`` is given;
the export reports what it skipped so the operator can decide.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from facebook_backend.config import PROJECT_ROOT
from facebook_backend.persistence.database import Database, default_database_url
from facebook_backend.persistence.job_store import DatabaseJobStore, ManifestJobStore


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

    jobs = [(raw, job_id) for raw, job_id in source.load_job_manifests()]
    batches = [(raw, batch_id) for raw, batch_id in source.load_batch_manifests()]

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
    parser = argparse.ArgumentParser(description="Export SQLite job state back to JSON manifests")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="project root (manifests live under it)")
    parser.add_argument("--database-url", default=None, help="default: FACEBOOK_DB_URL or data/facebook_backend.db")
    parser.add_argument("--apply", action="store_true", help="write the files (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run (the default)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing manifest files")
    parser.add_argument("--report", default=None, help="write the JSON report to this path")
    args = parser.parse_args(argv)
    apply = bool(args.apply) and not bool(args.dry_run)
    root = Path(args.root).resolve()
    database = Database.open(args.database_url or default_database_url(root))
    try:
        report = export_to_manifests(database, root, apply=apply, overwrite=args.overwrite)
    finally:
        database.dispose()
    payload = json.dumps(report.as_dict(), indent=2, ensure_ascii=False)
    print(payload)
    if args.report:
        Path(args.report).write_text(payload + "\n", encoding="utf-8")
    if not apply:
        print("\ndry-run: nothing written. Re-run with --apply to export.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
