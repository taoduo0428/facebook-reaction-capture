"""The job/batch state-store seam (manifest vs SQLite).

The point of these tests is the switch itself: ``JobManager`` must behave
identically whether its state lives in ``api-jobs/*.job.json`` or in the
``jobs`` table, and the database store must round-trip every field the recovery
logic reads (process identity, transport cleanup, attempt dirs, batch relation).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import facebook_capture_api as api
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.job_store import (
    DatabaseJobStore,
    ManifestJobStore,
    batch_payload,
    job_payload,
    open_store,
)
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import JobRepository, RepositoryError

BASE_URL = "http://127.0.0.1:8765"

JOB: dict = {
    "job_id": "fb_store01",
    "status": "running",
    "created_at": "2026-01-01T00:00:00Z",
    "started_at": "2026-01-01T00:00:01Z",
    "finished_at": None,
    "cancel_requested_at": None,
    "request": {
        "post_url": "https://www.facebook.com/x/posts/1",
        "collect": ["reactions"],
        "reaction_scroll_limit": 50,
    },
    "completion_scope": "terminal",
    "error": None,
    "run_dir": "runs/fb_store01",
    "capture_run_dir": "runs/fb_store01/attempt-01",
    "attempt_dirs": ["runs/fb_store01/attempt-01"],
    "batch_id": None,
    "batch_index": None,
    "worker_id": "worker-00",
    "serial": "emulator-5554",
    "attempts": 1,
    "failover_count": 0,
    "last_worker_id": "worker-00",
    "last_serial": "emulator-5554",
    "checkpoint": {"page": 3},
    "recovery_state": None,
    "process_pid": 4242,
    "process_creation_time": 1700000000.0,
    "process_started_at": "2026-01-01T00:00:02Z",
    "process_state": "running",
    "transport_cleanup_state": "restored",
    "transport_cleanup_error": None,
    "submission_key": "key-1",
}


class ManifestStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ManifestJobStore(self.root)

    def test_manifest_payload_keeps_the_historical_field_set(self):
        payload = job_payload(JOB)
        self.assertEqual(
            list(payload),
            [
                "version",
                "job_id",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "cancel_requested_at",
                "request",
                "completion_scope",
                "error",
                "run_dir",
                "capture_run_dir",
                "attempt_dirs",
                "batch_id",
                "batch_index",
                "worker_id",
                "serial",
                "attempts",
                "failover_count",
                "last_worker_id",
                "last_serial",
                "checkpoint",
                "recovery_state",
                "process_pid",
                "process_creation_time",
                "process_started_at",
                "process_state",
                "transport_cleanup_state",
                "transport_cleanup_error",
                "submission_key",
            ],
        )
        # The result and the scheduler pinning column were never in a manifest.
        self.assertNotIn("result", payload)
        self.assertNotIn("pinned_device_id", payload)

    def test_save_and_load_round_trip(self):
        self.store.save_job(JOB)
        path = self.store.job_path("fb_store01")
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.name, "api-jobs")
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["process_pid"], 4242)
        loaded = list(self.store.load_job_manifests())
        self.assertEqual(len(loaded), 1)
        raw, expected_id = loaded[0]
        self.assertEqual(expected_id, "fb_store01")
        self.assertEqual(raw["status"], "running")
        self.assertEqual(raw["attempt_dirs"], ["runs/fb_store01/attempt-01"])

    def test_paths_match_the_legacy_helpers(self):
        with mock.patch.object(api, "BASE_DIR", self.root):
            self.assertEqual(api._job_manifest_path("fb_store01"), self.store.job_path("fb_store01"))
            self.assertEqual(api._batch_manifest_path("batch_x"), self.store.batch_path("batch_x"))

    def test_broken_files_are_skipped_not_fatal(self):
        self.store.save_job(JOB)
        (self.store.jobs_dir / "fb_broken.job.json").write_text("{not json", encoding="utf-8")
        (self.store.jobs_dir / "fb_array.job.json").write_text("[]", encoding="utf-8")
        loaded = [expected for _raw, expected in self.store.load_job_manifests()]
        self.assertEqual(loaded, ["fb_store01"])

    def test_batch_round_trip(self):
        batch = {
            "batch_id": "batch_store",
            "created_at": "2026-01-01T00:00:00Z",
            "job_ids": ["fb_a", "fb_b"],
            "run_dir": "runs/batch_store",
            "completion_scope": "terminal",
        }
        self.store.save_batch(batch)
        self.assertEqual(json.loads(self.store.batch_path("batch_store").read_text("utf-8")), batch_payload(batch))
        raw, expected = next(iter(self.store.load_batch_manifests()))
        self.assertEqual(expected, "batch_store")
        self.assertEqual(raw["job_ids"], ["fb_a", "fb_b"])


class DatabaseStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'store.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)
        self.store = DatabaseJobStore(self.database)

    def test_round_trip_preserves_every_field(self):
        self.store.save_job(JOB)
        raw, expected_id = next(iter(self.store.load_job_manifests()))
        self.assertEqual(expected_id, "fb_store01")
        for key in (
            "status",
            "created_at",
            "started_at",
            "request",
            "completion_scope",
            "run_dir",
            "capture_run_dir",
            "attempt_dirs",
            "worker_id",
            "serial",
            "attempts",
            "failover_count",
            "last_worker_id",
            "last_serial",
            "checkpoint",
            "process_pid",
            "process_creation_time",
            "process_started_at",
            "process_state",
            "transport_cleanup_state",
            "submission_key",
        ):
            with self.subTest(field=key):
                self.assertEqual(raw[key], JOB[key])

    def test_save_is_an_upsert(self):
        self.store.save_job(JOB)
        self.store.save_job(dict(JOB, status="completed", finished_at="2026-01-01T00:05:00Z"))
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 1)
        raw, _ = next(iter(self.store.load_job_manifests()))
        self.assertEqual(raw["status"], "completed")
        self.assertEqual(raw["finished_at"], "2026-01-01T00:05:00Z")

    def test_error_is_stored_as_json_and_read_back(self):
        self.store.save_job(dict(JOB, status="failed", error={"message": "boom", "code": 7}))
        raw, _ = next(iter(self.store.load_job_manifests()))
        self.assertEqual(raw["error"], {"message": "boom", "code": 7})

    def test_batch_relations_round_trip_in_order(self):
        self.store.save_job(dict(JOB, job_id="fb_a", status="queued"))
        self.store.save_job(dict(JOB, job_id="fb_b", status="queued"))
        self.store.save_batch(
            {
                "batch_id": "batch_store",
                "created_at": "2026-01-01T00:00:00Z",
                "job_ids": ["fb_b", "fb_a"],
                "run_dir": "runs/batch_store",
                "completion_scope": "terminal",
            }
        )
        raw, expected = next(iter(self.store.load_batch_manifests()))
        self.assertEqual(expected, "batch_store")
        self.assertEqual(raw["job_ids"], ["fb_b", "fb_a"])
        self.assertEqual(raw["run_dir"], "runs/batch_store")

    def test_status_changes_are_appended_as_events(self):
        from facebook_backend.persistence.repositories import EventRepository

        self.store.save_job(dict(JOB, status="queued"))
        self.store.save_job(dict(JOB, status="running"))
        self.store.save_job(dict(JOB, status="completed"))
        # Saving the same status again must not invent a transition.
        self.store.save_job(dict(JOB, status="completed"))
        with self.database.session() as session:
            events = EventRepository(session).list_for_job("fb_store01")
        self.assertEqual(
            [event["event_type"] for event in events],
            ["job.created", "job.status_changed", "job.status_changed"],
        )
        self.assertEqual(events[0]["payload"], {"status": "queued"})
        self.assertEqual(events[1]["payload"], {"from": "queued", "to": "running"})
        self.assertEqual(events[2]["payload"], {"from": "running", "to": "completed"})

    def test_event_carries_the_trace_and_batch_context(self):
        from facebook_backend.observability import context
        from facebook_backend.persistence.repositories import EventRepository

        with context.bound(trace_id="4bf92f3577b34da6a3ce929d0e0e4736", batch_id="batch_x"):
            self.store.save_job(dict(JOB, status="queued"))
        with self.database.session() as session:
            events = EventRepository(session).list_for_job("fb_store01")
        self.assertEqual(events[0]["trace_id"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(events[0]["batch_id"], "batch_x")

    def test_batch_with_unknown_job_is_rejected(self):
        with self.assertRaises(RepositoryError):
            self.store.save_batch({"batch_id": "batch_x", "job_ids": ["fb_missing"]})


class OpenStoreTests(unittest.TestCase):
    def test_manifest_is_the_default(self):
        store = open_store("manifest", root=Path("."))
        self.assertIsInstance(store, ManifestJobStore)

    def test_database_requires_a_database(self):
        with self.assertRaises(ValueError):
            open_store("database", root=Path("."))

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            open_store("redis", root=Path("."))


class ManagerWithDatabaseStoreTests(unittest.TestCase):
    """The real switch: JobManager backed by SQLite instead of manifests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        base = mock.patch.object(api, "BASE_DIR", self.root)
        base.start()
        self.addCleanup(base.stop)
        starter = mock.patch.object(api.JobManager, "_start_worker", lambda self, worker_id: None)
        starter.start()
        self.addCleanup(starter.stop)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'state.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)
        self.store = DatabaseJobStore(self.database)

    def manager(self) -> api.JobManager:
        return api.JobManager("emulator-5554", store=self.store)

    def test_submit_writes_the_database_and_no_manifest(self):
        manager = self.manager()
        job = manager.submit({"post_url": "https://www.facebook.com/x/posts/1"})
        with self.database.session() as session:
            row = JobRepository(session).get(job["job_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "queued")
        self.assertFalse((self.root / "api-jobs").exists(), "database mode must not write manifests")

    def test_restart_restores_jobs_from_the_database(self):
        first = self.manager()
        job = first.submit({"post_url": "https://www.facebook.com/x/posts/1"})
        restarted = self.manager()
        self.assertIn(job["job_id"], restarted._jobs)
        self.assertEqual(restarted.public_job(job["job_id"])["status"], "queued")
        self.assertEqual(restarted.health()["queued_jobs"], 1)

    def test_cancel_updates_the_database(self):
        manager = self.manager()
        job = manager.submit({"post_url": "https://www.facebook.com/x/posts/1"})
        manager.cancel(job["job_id"])
        with self.database.session() as session:
            row = JobRepository(session).get(job["job_id"])
        self.assertEqual(row["status"], "cancelled")
        restarted = self.manager()
        self.assertEqual(restarted.public_job(job["job_id"])["status"], "cancelled")

    def test_batch_submission_persists_relations(self):
        manager = self.manager()
        batch = manager.submit_batch(
            {
                "post_urls": [
                    "https://www.facebook.com/x/posts/1",
                    "https://www.facebook.com/x/posts/2",
                ]
            }
        )
        from facebook_backend.persistence.repositories import BatchRepository

        with self.database.session() as session:
            self.assertEqual(BatchRepository(session).job_ids(batch["batch_id"]), [p["job_id"] for p in batch["posts"]])
        restarted = self.manager()
        self.assertIn(batch["batch_id"], restarted._batches)
        progress = restarted.batch_progress(batch["batch_id"])
        self.assertEqual(progress["total_posts"], 2)
        self.assertEqual([post["status"] for post in progress["posts"]], ["queued", "queued"])
        self.assertEqual(restarted._queue.qsize(), 2)
        self.assertFalse((self.root / "api-batches").exists())

    def test_batch_event_failure_rolls_back_jobs_parent_links_and_events(self):
        from facebook_backend.persistence.repositories import BatchRepository, EventRepository
        from facebook_backend.persistence.models import BatchJob
        from sqlalchemy import select

        manager = self.manager()
        original = self.store._record_acceptance
        recorded = []

        def fail_second_child(session, job_id, batch_id):
            recorded.append(job_id)
            if len(recorded) == 2:
                raise OSError("injected event write failure")
            return original(session, job_id, batch_id)

        with mock.patch.object(self.store, "_record_acceptance", fail_second_child), self.assertRaises(OSError):
            manager.submit_batch({"post_urls": [
                "https://www.facebook.com/x/posts/1", "https://www.facebook.com/x/posts/2"
            ]})
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).count(), 0)
            self.assertEqual(BatchRepository(session).count(), 0)
            self.assertEqual(EventRepository(session).count(), 0)
            self.assertIsNone(session.scalars(select(BatchJob)).first())
        self.assertEqual(manager._queue.qsize(), 0)
        self.assertFalse(manager._jobs)
        self.assertFalse(manager._batches)

    def test_stale_file_cannot_authorize_a_db_child_without_a_membership(self):
        from facebook_backend.persistence.repositories import BatchRepository

        with self.database.session() as session:
            BatchRepository(session).create(batch_id="batch_incomplete", status="queued")
            JobRepository(session).create(job_id="fb_orphan", status="queued", batch_id="batch_incomplete",
                batch_index=1, payload={"request": {"post_url": "https://www.facebook.com/x/posts/1"}})
        ManifestJobStore(self.root).save_batch({"batch_id": "batch_incomplete", "job_ids": ["fb_orphan"]})
        with self.assertRaisesRegex(RepositoryError, "batch membership"):
            self.manager()
        with self.database.session() as session:
            self.assertEqual(JobRepository(session).require("fb_orphan")["status"], "queued")


class LifespanStoreSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.url = f"sqlite+pysqlite:///{(self.root / 'api.db').as_posix()}"
        env = mock.patch.dict("os.environ", {"FACEBOOK_DB_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        starter = mock.patch.object(api.JobManager, "_start_worker", lambda self, worker_id: None)
        starter.start()
        self.addCleanup(starter.stop)

    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        project = Path(__file__).resolve().parents[1]
        config = Config(str(project / "alembic.ini"))
        config.set_main_option(
            "script_location", str(project / "facebook_backend" / "persistence" / "migrations")
        )
        config.set_main_option("sqlalchemy.url", self.url)
        command.upgrade(config, "head")

    def test_http_submission_lands_in_the_database(self):
        """settings -> store -> HTTP -> SQLite, through the real lifespan."""
        from fastapi.testclient import TestClient

        from facebook_backend.config import Settings
        from facebook_backend.main import create_app

        self.migrate()
        app = create_app(
            settings=Settings(
                base_dir=self.root, load_local_config=False, job_store="database", database_url=self.url
            )
        )
        client = TestClient(app, base_url=BASE_URL)
        client.__enter__()
        try:
            self.assertEqual(app.state.manager.store.name, "database")
            response = client.post(
                "/v1/jobs",
                content=json.dumps({"post_url": "https://www.facebook.com/x/posts/1"}),
                headers={"content-type": "application/json"},
            )
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job_id"]
            # Read it back over a separate connection: the row is really there.
            database = Database.open(self.url)
            try:
                with database.session() as session:
                    row = JobRepository(session).get(job_id)
            finally:
                database.dispose()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "queued")
            self.assertEqual(client.get(f"/v1/jobs/{job_id}").json()["job_id"], job_id)
        finally:
            client.__exit__(None, None, None)

    def test_unmigrated_database_fails_with_an_actionable_message(self):
        from fastapi.testclient import TestClient

        from facebook_backend.config import Settings
        from facebook_backend.main import create_app

        app = create_app(
            settings=Settings(
                base_dir=self.root, load_local_config=False,
                job_store="database",
                database_url=f"sqlite+pysqlite:///{(self.root / 'empty.db').as_posix()}",
            )
        )
        with self.assertRaises(RuntimeError) as caught:
            with TestClient(app, base_url=BASE_URL):
                pass
        self.assertIn("alembic upgrade head", str(caught.exception))

    def test_unknown_store_kind_is_rejected(self):
        from facebook_backend.config import Settings
        from facebook_backend.main import _open_state_store

        with self.assertRaises(RuntimeError):
            _open_state_store(Settings(load_local_config=False, job_store="redis"))


class ManifestExportTests(unittest.TestCase):
    """Database -> manifests, so switching the store stays reversible."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'state.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)
        self.store = DatabaseJobStore(self.database)
        self.store.save_job(dict(JOB, job_id="fb_a", status="completed"))
        self.store.save_job(dict(JOB, job_id="fb_b", status="queued"))
        self.store.save_batch({"batch_id": "batch_a", "job_ids": ["fb_a", "fb_b"]})

    def test_dry_run_writes_nothing(self):
        from facebook_backend.persistence.manifest_export import export_to_manifests

        report = export_to_manifests(self.database, self.root, apply=False)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.jobs_written, 2)
        self.assertEqual(report.batches_written, 1)
        self.assertFalse((self.root / "api-jobs").exists())

    def test_apply_then_round_trip(self):
        from facebook_backend.persistence.manifest_export import export_to_manifests

        report = export_to_manifests(self.database, self.root, apply=True)
        self.assertEqual(report.jobs_written, 2)
        manifests = ManifestJobStore(self.root)
        loaded = {expected: raw for raw, expected in manifests.load_job_manifests()}
        self.assertEqual(set(loaded), {"fb_a", "fb_b"})
        self.assertEqual(loaded["fb_a"]["status"], "completed")
        batch_raw, batch_id = next(iter(manifests.load_batch_manifests()))
        self.assertEqual(batch_id, "batch_a")
        self.assertEqual(batch_raw["job_ids"], ["fb_a", "fb_b"])

    def test_existing_manifests_are_not_overwritten_by_default(self):
        from facebook_backend.persistence.manifest_export import export_to_manifests

        export_to_manifests(self.database, self.root, apply=True)
        report = export_to_manifests(self.database, self.root, apply=True)
        self.assertEqual(report.jobs_written, 0)
        self.assertEqual(report.jobs_skipped, 2)
        self.assertEqual(sorted(report.skipped_jobs), ["fb_a", "fb_b"])


if __name__ == "__main__":
    unittest.main()
