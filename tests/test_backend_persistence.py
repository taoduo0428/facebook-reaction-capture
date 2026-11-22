"""Persistence layer tests (Phase 3).

Covers the schema/migration contract, the SQLite pragmas, session lifecycle,
optimistic concurrency, lease fencing and the offline manifest import
(dry-run, idempotency, blocking, conflicts, backup and rollback).

Every test runs against a real SQLite file in a temporary directory — the same
engine configuration production uses, not an in-memory shortcut.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from facebook_backend.persistence import legacy_manifest_import as importer
from facebook_backend.persistence.database import Database, default_database_url, sqlite_pragmas
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import (
    ArtifactRepository,
    BatchRepository,
    ConcurrentUpdateError,
    DeviceRepository,
    DuplicateRecordError,
    EventRepository,
    JobRepository,
    LeaseConflictError,
    LeaseRepository,
    RecordNotFoundError,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "alembic_version",
    "artifact_integrity",
    "artifacts",
    "batch_jobs",
    "batches",
    "device_leases",
    "devices",
    "job_attempts",
    "job_events",
    "jobs",
}


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "facebook_backend" / "persistence" / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade_database(url: str) -> None:
    with mock.patch.dict(os.environ, {"FACEBOOK_DB_URL": url}, clear=False):
        command.upgrade(alembic_config(url), "head")


class PersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_url = f"sqlite+pysqlite:///{(self.root / 'test.db').as_posix()}"
        self.database = Database.open(self.db_url)
        self.addCleanup(self.database.dispose)
        self.database.create_all()


class SchemaAndMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_alembic_upgrade_creates_the_full_schema(self):
        url = f"sqlite+pysqlite:///{(self.root / 'migrated.db').as_posix()}"
        upgrade_database(url)
        database = Database.open(url)
        self.addCleanup(database.dispose)
        tables = set(inspect(database.engine).get_table_names())
        self.assertEqual(tables, EXPECTED_TABLES)
        with database.engine.connect() as connection:
            version = connection.execute(text("select version_num from alembic_version")).scalar()
        self.assertEqual(version, "0004_job_event_context")

    def test_migration_and_models_do_not_drift(self):
        migrated_url = f"sqlite+pysqlite:///{(self.root / 'migrated.db').as_posix()}"
        upgrade_database(migrated_url)
        migrated = Database.open(migrated_url)
        self.addCleanup(migrated.dispose)

        declared_url = f"sqlite+pysqlite:///{(self.root / 'declared.db').as_posix()}"
        declared = Database.open(declared_url)
        self.addCleanup(declared.dispose)
        Base.metadata.create_all(declared.engine)

        migrated_inspector = inspect(migrated.engine)
        declared_inspector = inspect(declared.engine)
        self.assertEqual(
            set(migrated_inspector.get_table_names()) - {"alembic_version"},
            set(declared_inspector.get_table_names()),
        )
        for table in declared_inspector.get_table_names():
            with self.subTest(table=table):
                migrated_columns = {c["name"] for c in migrated_inspector.get_columns(table)}
                declared_columns = {c["name"] for c in declared_inspector.get_columns(table)}
                self.assertEqual(migrated_columns, declared_columns)
                migrated_indexes = {i["name"] for i in migrated_inspector.get_indexes(table)}
                declared_indexes = {i["name"] for i in declared_inspector.get_indexes(table)}
                self.assertEqual(migrated_indexes, declared_indexes)
                self.assertEqual(
                    {fk["name"] for fk in migrated_inspector.get_foreign_keys(table)},
                    {fk["name"] for fk in declared_inspector.get_foreign_keys(table)},
                )

    def test_sqlite_pragmas_are_applied(self):
        url = f"sqlite+pysqlite:///{(self.root / 'pragmas.db').as_posix()}"
        database = Database.open(url)
        self.addCleanup(database.dispose)
        pragmas = sqlite_pragmas(database)
        self.assertEqual(pragmas["journal_mode"].lower(), "wal")
        self.assertEqual(pragmas["foreign_keys"], "1")
        self.assertEqual(pragmas["busy_timeout"], "30000")
        self.assertEqual(pragmas["synchronous"], "1")  # NORMAL

    def test_foreign_keys_are_enforced(self):
        database = Database.open(f"sqlite+pysqlite:///{(self.root / 'fk.db').as_posix()}")
        self.addCleanup(database.dispose)
        Base.metadata.create_all(database.engine)
        with self.assertRaises(IntegrityError):
            with database.session() as session:
                JobRepository(session).create(
                    job_id="fb_fk", status="queued", payload={}, batch_id="batch_missing"
                )

    def test_only_one_active_lease_per_device(self):
        database = Database.open(f"sqlite+pysqlite:///{(self.root / 'lease.db').as_posix()}")
        self.addCleanup(database.dispose)
        Base.metadata.create_all(database.engine)
        with database.session() as session:
            DeviceRepository(session).upsert(device_id="dev-1", worker_id="w1", serial="emulator-1")
        with database.session() as session:
            LeaseRepository(session).acquire(device_id="dev-1", lease_token="a")
        with self.assertRaises(LeaseConflictError):
            with database.session() as session:
                LeaseRepository(session).acquire(device_id="dev-1", lease_token="b")


class SessionLifecycleTests(PersistenceTestCase):
    def test_commit_on_success(self):
        with self.database.session() as session:
            JobRepository(session).create(job_id="fb_1", status="queued", payload={"a": 1})
        with self.database.session() as session:
            self.assertIsNotNone(JobRepository(session).get("fb_1"))

    def test_rollback_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.database.session() as session:
                JobRepository(session).create(job_id="fb_2", status="queued", payload={})
                raise RuntimeError("boom")
        with self.database.session() as session:
            self.assertIsNone(JobRepository(session).get("fb_2"))

    def test_sessions_are_closed_after_the_unit_of_work(self):
        with self.database.session() as session:
            session.execute(text("select 1"))
            self.assertTrue(session.in_transaction())
        self.assertFalse(session.in_transaction())

    def test_batch_creation_is_atomic(self):
        with self.assertRaises(DuplicateRecordError):
            with self.database.session() as session:
                jobs = [
                    {"job_id": "fb_a", "status": "queued", "payload": {}},
                    {"job_id": "fb_a", "status": "queued", "payload": {}},
                ]
                BatchRepository(session).create_with_jobs(
                    batch_id="batch_x", status="queued", jobs=jobs
                )
        with self.database.session() as session:
            self.assertIsNone(BatchRepository(session).get("batch_x"))
            self.assertEqual(JobRepository(session).count(), 0)


class JobRepositoryTests(PersistenceTestCase):
    def test_create_and_read_back(self):
        with self.database.session() as session:
            repo = JobRepository(session)
            created = repo.create(
                job_id="fb_1",
                status="queued",
                payload={"request": {"post_url": "https://www.facebook.com/x/posts/1"}},
                run_dir="runs/fb_1",
            )
            self.assertEqual(created["status"], "queued")
            self.assertEqual(created["payload"]["request"]["post_url"], "https://www.facebook.com/x/posts/1")
            self.assertEqual(repo.count(), 1)
            self.assertEqual(repo.latest()["id"], "fb_1")
            self.assertEqual(repo.count("queued"), 1)
            self.assertEqual(repo.count("running"), 0)

    def test_duplicate_create_is_rejected(self):
        with self.database.session() as session:
            repo = JobRepository(session)
            repo.create(job_id="fb_1", status="queued", payload={})
        with self.assertRaises(DuplicateRecordError):
            with self.database.session() as session:
                JobRepository(session).create(job_id="fb_1", status="queued", payload={})

    def test_optimistic_concurrency_rejects_the_stale_writer(self):
        with self.database.session() as session:
            JobRepository(session).create(job_id="fb_1", status="queued", payload={})
        with self.database.session() as first:
            job = JobRepository(first).get("fb_1")
            stale_version = job["version"]
            JobRepository(first).set_status("fb_1", "running", expected_version=stale_version)
        with self.assertRaises(ConcurrentUpdateError):
            with self.database.session() as second:
                JobRepository(second).set_status("fb_1", "cancelled", expected_version=stale_version)
        with self.database.session() as session:
            final = JobRepository(session).require("fb_1")
        self.assertEqual(final["status"], "running")
        self.assertEqual(final["version"], stale_version + 1)

    def test_update_missing_job_raises(self):
        with self.assertRaises(RecordNotFoundError):
            with self.database.session() as session:
                JobRepository(session).set_status("fb_missing", "running")

    def test_attempts_round_trip(self):
        with self.database.session() as session:
            repo = JobRepository(session)
            repo.create(job_id="fb_1", status="running", payload={})
            attempt = repo.record_attempt(
                job_id="fb_1",
                attempt_index=1,
                worker_id="worker-01",
                device_serial="emulator-5554",
                process_id=4242,
                process_creation_time=1700000000.0,
            )
            repo.finish_attempt(attempt["attempt_id"], exit_code=0, result_status="completed")
        with self.database.session() as session:
            attempts = JobRepository(session).attempts("fb_1")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["exit_code"], 0)
        self.assertEqual(attempts[0]["result_status"], "completed")
        self.assertEqual(attempts[0]["process_id"], 4242)

    def test_events_are_append_only(self):
        with self.database.session() as session:
            repo = JobRepository(session)
            repo.create(job_id="fb_1", status="queued", payload={})
            events = EventRepository(session)
            first = events.append(event_type="created", job_id="fb_1", payload={"a": 1})
            second = events.append(event_type="status", job_id="fb_1", payload={"b": 2})
            self.assertLess(first["event_id"], second["event_id"])
            self.assertEqual(events.count(), 2)
        with self.database.session() as session:
            listed = EventRepository(session).list_for_job("fb_1")
        self.assertEqual([event["event_type"] for event in listed], ["created", "status"])


class BatchRepositoryTests(PersistenceTestCase):
    def test_create_with_jobs_writes_the_relation(self):
        with self.database.session() as session:
            repo = BatchRepository(session)
            batch = repo.create_with_jobs(
                batch_id="batch_1",
                status="queued",
                jobs=[
                    {"job_id": "fb_a", "status": "queued", "payload": {}},
                    {"job_id": "fb_b", "status": "completed", "payload": {}},
                ],
            )
            self.assertEqual(batch["total_jobs"], 2)
            self.assertEqual(repo.job_ids("batch_1"), ["fb_a", "fb_b"])
            self.assertEqual(JobRepository(session).require("fb_a")["batch_index"], 1)

    def test_recompute_counts_and_status(self):
        with self.database.session() as session:
            batches = BatchRepository(session)
            jobs = JobRepository(session)
            batches.create_with_jobs(
                batch_id="batch_1",
                status="queued",
                jobs=[
                    {"job_id": "fb_a", "status": "completed", "payload": {}},
                    {"job_id": "fb_b", "status": "failed", "payload": {}},
                ],
            )
            updated = batches.recompute_counts("batch_1")
            self.assertEqual(updated["completed_jobs"], 1)
            self.assertEqual(updated["failed_jobs"], 1)
            self.assertEqual(updated["status"], "completed_with_errors")
            jobs.set_status("fb_b", "completed")
            updated = batches.recompute_counts("batch_1")
            self.assertEqual(updated["status"], "completed")

    def test_latest_returns_the_newest_batch(self):
        with self.database.session() as session:
            repo = BatchRepository(session)
            repo.create(batch_id="batch_1", status="queued", created_at="2026-01-01T00:00:00Z")
            repo.create(batch_id="batch_2", status="queued", created_at="2026-02-01T00:00:00Z")
            self.assertEqual(repo.latest()["id"], "batch_2")


class ArtifactRepositoryTests(PersistenceTestCase):
    def test_upsert_is_idempotent_per_name(self):
        with self.database.session() as session:
            jobs = JobRepository(session)
            jobs.create(job_id="fb_1", status="completed", payload={})
            artifacts = ArtifactRepository(session)
            first = artifacts.upsert(
                job_id="fb_1",
                artifact_name="facebook-reactor-users.csv",
                artifact_path="runs/fb_1/analysis/facebook-reactor-users.csv",
                size=10,
                sha256="a" * 64,
                verification_status="verified",
            )
            second = artifacts.upsert(
                job_id="fb_1",
                artifact_name="facebook-reactor-users.csv",
                artifact_path="runs/fb_1/analysis/facebook-reactor-users.csv",
                size=20,
                sha256="b" * 64,
                verification_status="verified",
            )
            self.assertEqual(first["artifact_id"], second["artifact_id"])
            self.assertEqual(artifacts.count(), 1)
            self.assertEqual(artifacts.list_for_job("fb_1")[0]["size"], 20)


class DeviceLeaseTests(PersistenceTestCase):
    def test_acquire_heartbeat_release_and_fencing(self):
        with self.database.session() as session:
            DeviceRepository(session).upsert(
                device_id="dev-1", worker_id="worker-01", serial="emulator-5554", capture_port=8081
            )
        with self.database.session() as session:
            leases = LeaseRepository(session)
            lease = leases.acquire(device_id="dev-1", job_id="fb_1", ttl_seconds=60)
            self.assertEqual(lease["fencing_version"], 1)
            self.assertIsNotNone(leases.active("dev-1"))
            with self.assertRaises(LeaseConflictError):
                leases.acquire(device_id="dev-1", job_id="fb_2")
            heartbeat = leases.heartbeat(lease["lease_token"], ttl_seconds=120)
            self.assertGreaterEqual(heartbeat["expires_at"], lease["expires_at"])
            released = leases.release(lease["lease_token"], reason="completed")
            self.assertIsNotNone(released["released_at"])
            self.assertIsNone(leases.active("dev-1"))
        with self.database.session() as session:
            again = LeaseRepository(session).acquire(device_id="dev-1", job_id="fb_2")
        self.assertEqual(again["fencing_version"], 2)

    def test_expired_lease_still_blocks_acquisition(self):
        # Expiry is not permission to take over: the old process and cleanup
        # must be verified first, so the database keeps the lease active.
        with self.database.session() as session:
            DeviceRepository(session).upsert(device_id="dev-1", worker_id="w1", serial="emulator-1")
            LeaseRepository(session).acquire(device_id="dev-1", job_id="fb_1", ttl_seconds=-1)
        with self.assertRaises(LeaseConflictError):
            with self.database.session() as session:
                LeaseRepository(session).acquire(device_id="dev-1", job_id="fb_2")

    def test_heartbeat_requires_an_active_lease(self):
        with self.assertRaises(RecordNotFoundError):
            with self.database.session() as session:
                LeaseRepository(session).heartbeat("lease_missing")


def write_job_manifest(
    root: Path,
    job_id: str,
    *,
    status: str = "completed",
    run_dir: str | None = None,
    **overrides,
) -> dict:
    manifest = {
        "version": 1,
        "job_id": job_id,
        "status": status,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:01Z",
        "finished_at": "2026-01-01T00:00:02Z",
        "request": {"post_url": "https://www.facebook.com/x/posts/1", "collect": ["reactions"]},
        "run_dir": run_dir,
        "attempts": 1,
        "failover_count": 0,
    }
    manifest.update(overrides)
    directory = root / "api-jobs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{job_id}.job.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def write_batch_manifest(root: Path, batch_id: str, job_ids: list[str], **overrides) -> dict:
    manifest = {
        "version": 1,
        "batch_id": batch_id,
        "created_at": "2026-01-01T00:00:00Z",
        "job_ids": job_ids,
        "run_dir": f"runs/{batch_id}",
    }
    manifest.update(overrides)
    directory = root / "api-batches"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{batch_id}.batch.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def write_required_artifacts(root: Path, job_id: str) -> None:
    analysis = root / "runs" / job_id / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "facebook-reactor-users.csv").write_text("user_id,name\n1,张三\n", encoding="utf-8")
    (analysis / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")


class LegacyImportTests(PersistenceTestCase):
    def _plan(self, identity: str = "absent"):
        return importer.scan(self.root, identity_probe=lambda pid, created: identity)

    def test_dry_run_writes_nothing(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        write_required_artifacts(self.root, "fb_1")
        write_batch_manifest(self.root, "batch_1", ["fb_1"])
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=True, service_probe=lambda: False
        )
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["manifest_jobs"], 1)
        self.assertEqual(report["imported_jobs"], 0)
        self.assertIsNone(report["backup_dir"])
        self.assertTrue(report["ok"])
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)
            self.assertEqual(BatchRepository(session).count(), 0)

    def test_apply_imports_jobs_batches_artifacts_and_events(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        write_required_artifacts(self.root, "fb_1")
        write_batch_manifest(self.root, "batch_1", ["fb_1"])
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=False, backup=False, service_probe=lambda: False
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["imported_jobs"], 1)
        self.assertEqual(report["imported_batches"], 1)
        self.assertEqual(report["imported_artifacts"], 2)
        self.assertTrue(report["counts_match"])
        with self.database.session() as session:
            job = JobRepository(session).require("fb_1")
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["batch_id"], "batch_1")
            self.assertEqual(job["attempt_count"], 1)
            artifacts = ArtifactRepository(session).list_for_job("fb_1")
            self.assertEqual({a["artifact_name"] for a in artifacts},
                             {"facebook-reactor-users.csv", "facebook-sample-patterns.json"})
            self.assertTrue(all(len(a["sha256"] or "") == 64 for a in artifacts))
            self.assertEqual({a["verification_status"] for a in artifacts}, {"unknown"})
            events = EventRepository(session).list_for_job("fb_1")
            self.assertEqual(events[0]["event_type"], "legacy-import")
            self.assertTrue(BatchRepository(session).job_ids("batch_1") == ["fb_1"])

    def test_reimport_is_idempotent(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        write_required_artifacts(self.root, "fb_1")
        write_batch_manifest(self.root, "batch_1", ["fb_1"])
        plan = self._plan()
        first = importer.apply_plan(
            self.database, plan, dry_run=False, backup=False, service_probe=lambda: False
        )
        with self.database.session() as session:
            initial_events = EventRepository(session).count()
        second = importer.apply_plan(
            self.database, plan, dry_run=False, backup=False, service_probe=lambda: False
        )
        with self.database.session() as session:
            self.assertEqual(EventRepository(session).count(), initial_events)
        self.assertEqual(second["imported_events"], 0)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["imported_jobs"], 0)
        self.assertEqual(second["skipped_jobs"], 1)
        self.assertEqual(second["skipped_batches"], 1)
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 1)
            self.assertEqual(BatchRepository(session).count(), 1)

    def test_running_job_with_live_process_is_blocked_not_resumed(self):
        write_job_manifest(
            self.root,
            "fb_1",
            status="running",
            run_dir="runs/fb_1",
            process_pid=4242,
            process_creation_time=1700000000.0,
        )
        plan = self._plan(identity="match")
        self.assertEqual(len(plan.blocked), 1)
        self.assertFalse(plan.jobs[0].safe_to_resume)
        report = importer.apply_plan(
            self.database, plan, dry_run=False, backup=False, service_probe=lambda: False
        )
        with self.database.session() as session:
            self.assertIsNone(JobRepository(session).get("fb_1"))
            self.assertEqual(EventRepository(session).count(), 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["imported_jobs"], 0)
        self.assertEqual(report["blocked"][0]["identity"], "match")

    def test_unverified_process_is_blocked(self):
        write_job_manifest(
            self.root,
            "fb_1",
            status="cancelling",
            run_dir="runs/fb_1",
            process_pid=4242,
        )
        plan = self._plan(identity="unknown")
        self.assertFalse(plan.jobs[0].safe_to_resume)
        self.assertEqual(plan.blocked[0]["identity"], "unknown")

    def test_cleared_process_is_safe_to_resume(self):
        write_job_manifest(
            self.root,
            "fb_1",
            status="running",
            run_dir="runs/fb_1",
            process_pid=4242,
            process_creation_time=1700000000.0,
        )
        plan = self._plan(identity="absent")
        self.assertEqual(plan.jobs[0].identity, "absent")
        self.assertFalse(plan.jobs[0].safe_to_resume)
        self.assertFalse(plan.ok)
        write_job_manifest(self.root, "fb_1", status="failed", process_pid=4242,
                           process_creation_time=1700000000.0, process_state="stopped")
        stopped = self._plan(identity="absent")
        self.assertTrue(stopped.jobs[0].safe_to_resume)
        self.assertEqual(stopped.blocked, ())

    def test_invalid_manifests_are_reported_and_skipped(self):
        write_job_manifest(self.root, "fb_bad_version", status="completed", version=2)
        write_job_manifest(self.root, "fb_unknown_status", status="exploded")
        (self.root / "api-jobs" / "fb_mismatch.job.json").write_text(
            json.dumps({"version": 1, "job_id": "fb_other", "status": "completed"}),
            encoding="utf-8",
        )
        plan = self._plan()
        self.assertEqual(plan.jobs, ())
        self.assertEqual(len(plan.invalid_jobs), 3)
        report = importer.apply_plan(
            self.database, plan, dry_run=False, backup=False, service_probe=lambda: False
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["imported_jobs"], 0)

    def test_batch_with_missing_jobs_is_an_orphan(self):
        write_batch_manifest(self.root, "batch_1", ["fb_missing"])
        plan = self._plan()
        self.assertEqual(plan.batches, ())
        self.assertEqual(len(plan.orphan_batches), 1)
        report = importer.apply_plan(
            self.database, plan, dry_run=False, backup=False, service_probe=lambda: False
        )
        self.assertEqual(report["imported_batches"], 0)
        self.assertFalse(report["ok"])

    def test_job_with_missing_batch_rejects_entire_plan(self):
        write_job_manifest(self.root, "fb_valid")
        write_job_manifest(self.root, "fb_1", status="completed", batch_id="batch_gone")
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=False, backup=False, service_probe=lambda: False
        )
        self.assertEqual(report["orphaned_job_batch_references"][0]["batch_id"], "batch_gone")
        self.assertFalse(report["ok"])
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)
            self.assertEqual(EventRepository(session).count(), 0)

    def test_refuses_to_apply_while_the_service_is_running(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=False, backup=False, service_probe=lambda: True
        )
        self.assertFalse(report["ok"])
        self.assertIn("stop it before importing", report["error"])
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)

    def test_force_never_allows_applying_while_the_service_runs(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        report = importer.apply_plan(
            self.database,
            self._plan(),
            dry_run=False,
            backup=False,
            service_probe=lambda: True,
            force=True,
        )
        self.assertFalse(report["ok"])
        self.assertFalse(report["forced_while_running"])
        self.assertEqual(report["imported_jobs"], 0)
        with self.assertRaises(SystemExit) as error:
            importer.build_parser().parse_args(["--apply", "--force"])
        self.assertEqual(error.exception.code, 2)

    def test_backup_copies_the_manifests(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        write_batch_manifest(self.root, "batch_1", ["fb_1"])
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=False, backup=True, service_probe=lambda: False
        )
        backup = Path(report["backup_dir"])
        self.assertTrue((backup / "api-jobs" / "fb_1.job.json").is_file())
        self.assertTrue((backup / "api-batches" / "batch_1.batch.json").is_file())
        self.assertTrue((self.root / "api-jobs" / "fb_1.job.json").is_file())

    def test_conflicting_existing_row_is_reported_not_overwritten(self):
        with self.database.session() as session:
            JobRepository(session).create(job_id="fb_1", status="failed", payload={})
        write_job_manifest(self.root, "fb_1", status="completed", run_dir="runs/fb_1")
        report = importer.apply_plan(
            self.database, self._plan(), dry_run=False, backup=False, service_probe=lambda: False
        )
        self.assertEqual(report["conflicts"][0]["job_id"], "fb_1")
        self.assertFalse(report["ok"])
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).require("fb_1")["status"], "failed")

    def test_failure_rolls_back_every_row(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        write_job_manifest(self.root, "fb_2", run_dir="runs/fb_2")
        original = JobRepository.create
        calls = {"count": 0}

        def flaky(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated write failure")
            return original(self, **kwargs)

        with mock.patch.object(JobRepository, "create", flaky):
            with self.assertRaises(RuntimeError):
                importer.apply_plan(
                    self.database, self._plan(), dry_run=False, backup=False, service_probe=lambda: False
                )
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)

    def test_cli_returns_non_zero_when_attention_is_required(self):
        write_job_manifest(self.root, "fb_1", run_dir="runs/fb_1")
        code = importer.main(["--root", str(self.root), "--database-url", self.db_url])
        self.assertEqual(code, 0)  # dry-run on a clean plan succeeds
        (self.root / "api-jobs" / "fb_bad.job.json").write_text(
            json.dumps({"version": 2, "job_id": "fb_bad"}), encoding="utf-8"
        )
        code = importer.main(["--root", str(self.root), "--database-url", self.db_url])
        self.assertEqual(code, 2)

    def test_relationship_errors_never_commit_unaffected_rows(self):
        for label, members, other, child in [
            ("duplicate", ["fb_a", "fb_a"], [], {}),
            ("overlap", ["fb_a"], ["fb_a"], {}),
            ("disagrees", ["fb_a"], ["fb_b"], {"batch_id": "batch_2"}),
            ("unlisted", ["fb_b"], [], {"batch_id": "batch_1"}),
            ("wrong-order", ["fb_a", "fb_b"], [], {"batch_index": 2}),
            ("boolean-index", ["fb_a", "fb_b"], [], {"batch_index": True}),
            ("index-without-batch", ["fb_b"], [], {"batch_index": 1}),
            ("unknown-child", ["fb_missing"], [], {}),
        ]:
            with self.subTest(label=label):
                root = self.root / label
                write_job_manifest(root, "fb_a", **child)
                write_job_manifest(root, "fb_b")
                write_batch_manifest(root, "batch_1", members)
                if other:
                    write_batch_manifest(root, "batch_2", other)
                plan = importer.scan(root)
                self.assertFalse(plan.ok)
                report = importer.apply_plan(self.database, plan, dry_run=False, service_probe=lambda: False)
                self.assertFalse(report["ok"])
                self.assertIsNone(report["backup_dir"])
                with self.database.session() as session:
                    for repo in (JobRepository, BatchRepository, EventRepository, ArtifactRepository):
                        self.assertEqual(repo(session).count(), 0)

    def test_unsafe_or_unknown_states_block_the_whole_apply(self):
        write_job_manifest(self.root, "fb_normal")
        for fields, identity in [
            ({"status": "cancelling", "process_state": "stopped"}, "absent"),
            ({"process_state": "unexpected"}, "absent"),
            ({"process_state": "stop-unverified"}, "absent"),
            ({"transport_cleanup_state": "pending"}, "absent"),
            ({"transport_cleanup_state": "unknown"}, "absent"),
            ({"process_pid": 4242}, "mismatch"),
            ({"process_pid": 4242}, "unknown"),
            ({"process_pid": -1}, "unknown"),
        ]:
            with self.subTest(fields=fields, identity=identity):
                write_job_manifest(self.root, "fb_suspect", **fields)
                report = importer.apply_plan(self.database, self._plan(identity), dry_run=False,
                                             service_probe=lambda: False)
                self.assertFalse(report["ok"])
                self.assertEqual(report["imported_jobs"], 0)
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)

    def test_failure_marker_and_submission_key_survive_store_reload(self):
        from facebook_backend.persistence.job_store import DatabaseJobStore

        raw = write_job_manifest(
            self.root, "fb_1", status="failed", submission_key="synthetic-dedup-key",
            recovery_state="failed-result-persistence", result=None, error={"reason": "I/O"},
            run_dir="runs/fb_1", capture_run_dir="runs/fb_1/first", attempt_dirs=["runs/fb_1/first"],
            checkpoint={"offset": 3}, last_worker_id="worker-0", last_serial="device-0",
            process_started_at="2026-01-01T00:00:01Z", process_state="stopped",
            transport_cleanup_state="restored", transport_cleanup_error="historical I/O detail",
            worker_id="worker-1", serial="device-1", pinned_device_id="device-1",
            failover_count=1, completion_scope=None, cancel_requested_at=None,
        )
        plan = self._plan()
        self.assertTrue(plan.ok)
        self.assertFalse(plan.jobs[0].safe_to_resume)
        report = importer.apply_plan(self.database, plan, dry_run=False, service_probe=lambda: False)
        self.assertTrue(report["ok"], report)
        loaded, = DatabaseJobStore(self.database).load_job_manifests()
        job, ident = loaded
        self.assertEqual(ident, "fb_1")
        for key, value in raw.items():
            with self.subTest(key=key):
                self.assertEqual(job[key], value)

    def test_projection_drift_fails_verify_and_rolls_back_other_rows(self):
        write_job_manifest(self.root, "fb_existing", run_dir="runs/fb_existing", submission_key="stable-key")
        write_required_artifacts(self.root, "fb_existing")
        write_batch_manifest(self.root, "batch_1", ["fb_existing"])
        first = importer.apply_plan(self.database, self._plan(), dry_run=False, service_probe=lambda: False)
        self.assertTrue(first["ok"], first)
        write_job_manifest(self.root, "fb_new")
        plan = self._plan()
        with self.database.session() as session:
            old_events = EventRepository(session).count()
        for label, statement, undo in [
            ("payload", "UPDATE jobs SET payload='{}'", None),
            ("result", "UPDATE jobs SET result='{}'", "UPDATE jobs SET result=NULL"),
            ("invalid result JSON", "UPDATE jobs SET result='broken json'", "UPDATE jobs SET result=NULL"),
            ("pinned_device_id", "UPDATE jobs SET pinned_device_id='other'", "UPDATE jobs SET pinned_device_id=NULL"),
            ("recovery_state", "UPDATE jobs SET recovery_state='changed'", "UPDATE jobs SET recovery_state=NULL"),
            ("batch_index", "UPDATE jobs SET batch_index=7", "UPDATE jobs SET batch_index=1"),
            ("completed_jobs", "UPDATE batches SET completed_jobs=0", "UPDATE batches SET completed_jobs=1"),
            ("completion_scope", "UPDATE batches SET completion_scope='drift'", "UPDATE batches SET completion_scope=NULL"),
            ("batch_jobs", "UPDATE batch_jobs SET position=8", "UPDATE batch_jobs SET position=1"),
            ("artifact size", "UPDATE artifacts SET size=size+2", "UPDATE artifacts SET size=size-2"),
        ]:
            with self.subTest(label=label):
                with self.database.session() as session:
                    original_payload = session.scalar(text("select payload from jobs where id='fb_existing'"))
                    session.execute(text(statement))
                verified = importer.apply_plan(self.database, plan, verify=True, service_probe=lambda: False)
                self.assertFalse(verified["ok"])
                self.assertTrue(verified["conflicts"])
                applied = importer.apply_plan(self.database, plan, dry_run=False, backup=False, service_probe=lambda: False)
                self.assertTrue(applied["rolled_back"])
                self.assertEqual(applied["imported_jobs"], 0)
                with self.database.session() as session:
                    self.assertIsNone(JobRepository(session).get("fb_new"))
                    self.assertEqual(EventRepository(session).count(), old_events)
                    if undo:
                        session.execute(text(undo))
                    else:
                        session.execute(text("UPDATE jobs SET payload=:payload"), {"payload": original_payload})

    def test_submission_key_collisions_fail_in_source_and_existing_database(self):
        write_job_manifest(self.root, "fb_a", submission_key="dup")
        write_job_manifest(self.root, "fb_b", submission_key="dup")
        self.assertFalse(self._plan().ok)
        write_job_manifest(self.root, "fb_b", submission_key="different")
        with self.database.session() as session:
            JobRepository(session).create(job_id="fb_external", status="failed", payload={"submission_key": "dup"})
        report = importer.apply_plan(self.database, self._plan(), dry_run=False, service_probe=lambda: False)
        self.assertTrue(report["rolled_back"])
        self.assertTrue(report["conflicts"])
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 1)

    def test_backup_must_be_new_and_disjoint_from_source(self):
        write_job_manifest(self.root, "fb_1")
        source = self.root / "api-jobs" / "fb_1.job.json"
        before = source.read_bytes()
        existing = self.root / "prior"
        existing.mkdir()
        marker = existing / "marker"
        marker.write_bytes(b"unchanged")
        for dest in (existing, source.parent / "nested"):
            with self.subTest(dest=dest):
                report = importer.apply_plan(self.database, self._plan(), dry_run=False, backup_dir=dest,
                                             service_probe=lambda: False)
                self.assertFalse(report["ok"])
                self.assertIn("backup", report["error"])
                self.assertEqual(before, source.read_bytes())
                self.assertEqual(list(existing.iterdir()), [marker])
                self.assertEqual(marker.read_bytes(), b"unchanged")
                self.assertFalse((source.parent / "nested").exists())
                with self.database.session() as session:
                    self.assertEqual(JobRepository(session).count(), 0)

    def test_changed_source_invalidates_a_prepared_plan(self):
        write_job_manifest(self.root, "fb_1", status="queued")
        plan = self._plan()
        write_job_manifest(self.root, "fb_1", status="completed")
        report = importer.apply_plan(self.database, plan, dry_run=False, service_probe=lambda: False)
        self.assertFalse(report["ok"])
        self.assertIn("changed", report["error"])
        self.assertEqual(report["imported_jobs"], 0)

    def test_cli_env_selects_migrated_target_verify_is_read_only(self):
        from contextlib import redirect_stdout
        import io

        target = self.root / "actual.db"
        url = f"sqlite+pysqlite:///{target.as_posix()}"
        upgrade_database(url)
        write_job_manifest(self.root, "fb_1", submission_key="env-key")
        backup = self.root / "snapshot"
        with (mock.patch.dict(os.environ, {"FACEBOOK_DB_URL": url}),
              mock.patch.object(importer, "_service_is_running", return_value=False),
              redirect_stdout(io.StringIO())):
            def invoke(*args):
                log = self.root / f"report-{len(list(self.root.glob('report-*')))}.json"
                code = importer.main(["--root", str(self.root), "--report", str(log), *args])
                return code, json.loads(log.read_text(encoding="utf-8"))

            dry_db = self.root / "absent" / "dry.db"
            code, dry = invoke("--dry-run", "--database-url", f"sqlite+pysqlite:///{dry_db.as_posix()}")
            self.assertEqual(code, 0)
            self.assertEqual(dry["imported_jobs"], 0)
            self.assertFalse(dry_db.parent.exists())
            code, missing = invoke("--verify")
            self.assertEqual(code, 2)
            self.assertEqual(missing["missing_jobs"], ["fb_1"])
            code, apply = invoke("--apply", "--backup", str(backup))
            self.assertEqual(code, 0, apply)
            self.assertEqual(apply["backup_dir"], str(backup))
            self.assertTrue((backup / "api-jobs" / "fb_1.job.json").is_file())
            before = target.read_bytes()
            code, verified = invoke("--verify")
            self.assertEqual(code, 0, verified)
            self.assertTrue(verified["counts_match"])
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse((self.root / "data" / "facebook_backend.db").exists())
            with self.assertRaises(SystemExit):
                invoke("--dry-run", "--apply")

    def test_late_reconciliation_failure_rolls_back_parent_child_events_and_artifacts(self):
        write_job_manifest(self.root, "fb_child", run_dir="runs/fb_child")
        write_required_artifacts(self.root, "fb_child")
        write_batch_manifest(self.root, "batch_1", ["fb_child"])
        original = BatchRepository.link_jobs

        def introduce_drift(repo, batch_id, ids):
            result = original(repo, batch_id, ids)
            repo.session.execute(text("UPDATE batch_jobs SET position=2"))
            return result

        with mock.patch.object(BatchRepository, "link_jobs", introduce_drift):
            report = importer.apply_plan(self.database, self._plan(), dry_run=False, service_probe=lambda: False)
        self.assertTrue(report["rolled_back"], report)
        self.assertEqual(report["imported_jobs"], 0)
        self.assertEqual(report["imported_events"], 0)
        with self.database.session() as session:
            for repo in (JobRepository, BatchRepository, EventRepository, ArtifactRepository):
                self.assertEqual(repo(session).count(), 0)
            self.assertEqual(session.scalar(text("select count(*) from batch_jobs")), 0)

    def test_unknown_fields_ambiguous_json_and_inconsistent_batch_state_are_rejected(self):
        write_job_manifest(self.root, "fb_1", status="queued", future_state={"reserved": True})
        self.assertIn("unmapped", self._plan().invalid_jobs[0]["reason"])
        write_job_manifest(self.root, "fb_1", status="queued")
        write_batch_manifest(self.root, "batch_1", ["fb_1"], status="completed")
        self.assertIn("disagrees", self._plan().invalid_batches[0]["reason"])
        path = self.root / "api-jobs" / "fb_1.job.json"
        path.write_text(path.read_text(encoding="utf-8").replace('"queued"', '"queued", "status": "failed"'),
                        encoding="utf-8")
        self.assertIn("duplicate JSON key", self._plan().invalid_jobs[0]["reason"])

    def test_unknown_existing_target_process_blocks_unrelated_new_import(self):
        write_job_manifest(self.root, "fb_new")
        with self.database.session() as session:
            JobRepository(session).create(job_id="fb_existing", status="failed", payload={}, process_pid=1234)
        with mock.patch.object(importer.process_manager, "identity_state", return_value="unknown"):
            report = importer.apply_plan(self.database, self._plan(), dry_run=False, service_probe=lambda: False)
        self.assertFalse(report["ok"])
        self.assertTrue(report["rolled_back"])
        with self.database.session() as session:
            self.assertEqual([j["id"] for j in JobRepository(session).list_all()], ["fb_existing"])

    def test_cli_rollback_projects_only_to_new_destination(self):
        from contextlib import redirect_stdout
        import io

        target = self.root / "migrated.db"
        url = f"sqlite+pysqlite:///{target.as_posix()}"
        upgrade_database(url)
        write_job_manifest(self.root, "fb_1", status="queued", started_at=None, finished_at=None, attempts=0)
        source = self.root / "api-jobs" / "fb_1.job.json"
        original = source.read_bytes()
        destination = self.root / "rollback-bundle"
        args = ["--root", str(self.root), "--rollback", "--rollback-dir", str(destination)]
        with (mock.patch.dict(os.environ, {"FACEBOOK_DB_URL": url}),
              mock.patch.object(importer, "_service_is_running", return_value=False), redirect_stdout(io.StringIO())):
            self.assertEqual(importer.main(["--root", str(self.root), "--apply"]), 0)
            report = self.root / "rollback-report.json"
            code = importer.main([*args, "--report", str(report)])
            details = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(code, 0, details)
            self.assertEqual(details["jobs_written"], 1)
            self.assertTrue(Path(details["backup"]).is_file())
            self.assertTrue((destination / "api-jobs" / source.name).is_file())
            self.assertNotEqual(importer.main(args), 0)
            self.assertEqual(source.read_bytes(), original)

    def test_cli_process_owns_state_and_produces_a_startable_database(self):
        import socket
        import subprocess
        import sys
        from facebook_backend.config import Settings
        from facebook_backend.persistence.state_startup import open_state_store, state_owner

        target = self.root / "cli.db"
        url = f"sqlite+pysqlite:///{target.as_posix()}"
        upgrade_database(url)
        write_job_manifest(self.root, "fb_new", status="queued", attempts=0, started_at=None, finished_at=None)
        env = dict(os.environ, FACEBOOK_DB_URL=url, PYTHONDONTWRITEBYTECODE="1")
        # Ask the OS for a free port and release it again.  A bound-but-not-listening
        # socket is not portable evidence of "nothing is running": macOS drops the
        # SYN instead of sending RST, so the probe would time out rather than get
        # a refusal.  A closed port refuses the connection on every platform.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = str(probe.getsockname()[1])
        if True:
            def cli(*args):
                result = subprocess.run([sys.executable, "-B", "-m", importer.__name__,
                                         "--root", str(self.root), "--api-port", port, *args],
                                        cwd=ROOT, env=env, capture_output=True, timeout=45,
                                        encoding="utf-8", errors="replace")
                return result.returncode, json.loads(result.stdout)

            with state_owner(self.root, url):
                code, busy = cli("--apply")
            self.assertEqual(code, 2, busy)
            self.assertIn("lock", busy["error"])
            for mode in ("--dry-run", "--apply", "--verify"):
                code, report = cli(mode)
                self.assertEqual(code, 0, report)
            with state_owner(self.root, url):
                store, database = open_state_store(Settings(base_dir=self.root, job_store="database", database_url=url))
                with database:
                    self.assertEqual([ident for _, ident in store.load_job_manifests()], ["fb_new"])
            code, rolled_back = cli("--rollback", "--rollback-dir", str(self.root / "separate-export"))
            self.assertEqual(code, 0, rolled_back)
            self.assertEqual(rolled_back["jobs_written"], 1)
            with socket.socket() as live_port:
                live_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                live_port.bind(("127.0.0.1", int(port)))
                live_port.listen()
                code, live = cli("--apply")
            self.assertEqual(code, 2, live)
            self.assertIn("stop", live["error"])

    def test_default_database_url_lives_under_data(self):
        self.assertIn("data", default_database_url(self.root).replace("\\", "/"))

    def test_console_output_survives_a_non_utf8_stream(self):
        """A cp1252 console must not turn a good dry-run into a failure."""
        from facebook_backend.persistence import legacy_manifest_import as importer

        class AsciiOnly:
            def __init__(self):
                self.chunks: list[str] = []

            def write(self, text):
                text.encode("ascii")  # raises UnicodeEncodeError on "→"
                self.chunks.append(text)

            def flush(self):
                pass

        stream = AsciiOnly()
        importer._emit("dropped job→batch links", stream=stream)
        self.assertEqual("".join(stream.chunks).strip(), "dropped job?batch links")


if __name__ == "__main__":
    unittest.main()
