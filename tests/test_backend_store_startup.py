"""Isolated startup, ownership and offline cutover contracts."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from facebook_backend.config import Settings
from facebook_backend.main import create_app
from facebook_backend.persistence.database import Database, default_database_path, default_database_url
from facebook_backend.persistence.job_store import ManifestJobStore
from facebook_backend.persistence.models import JobEvent
from facebook_backend.persistence.state_startup import (
    manifest_digest, open_state_store, state_owner, validate_schema, write_cutover_receipt,
)


class StoreStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.settings = Settings(base_dir=self.root, load_local_config=False)
        self.path = default_database_path(self.root)

    def bootstrap(self):
        store, database = open_state_store(self.settings)
        self.assertEqual(store.name, "database")
        self.addCleanup(database.dispose)
        return store, database

    def test_default_migrates_own_file_even_with_foreign_environment(self):
        other = self.root / "external.db"
        with mock.patch.dict(os.environ, {"FACEBOOK_DB_URL": f"sqlite:///{other.as_posix()}"}, clear=True):
            self.assertEqual(Settings.from_env().job_store, "auto")
            _, database = self.bootstrap()
            validate_schema(database)
            database.dispose()
            with mock.patch("alembic.command.upgrade", side_effect=AssertionError("migrated twice")):
                self.bootstrap()
        self.assertTrue(self.path.is_file())
        self.assertFalse(other.exists())

    def test_absent_or_empty_custom_database_and_explicit_mode_fail_closed(self):
        for settings in (self.settings.with_overrides(job_store="database"),
                         self.settings.with_overrides(database_url=f"sqlite:///{self.root.as_posix()}/other.db")):
            with self.subTest(settings=settings), self.assertRaisesRegex(RuntimeError, "upgrade head"):
                open_state_store(settings)
        self.assertFalse(self.path.exists())
        self.assertFalse((self.root / "other.db").exists())
        self.path.parent.mkdir()
        self.path.touch()
        with self.assertRaisesRegex(RuntimeError, "Alembic head"):
            self.bootstrap()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema").fetchall(), [])
        db.close()

    def test_missing_previously_initialized_database_is_not_replaced(self):
        _, database = self.bootstrap()
        database.dispose()
        self.path.rename(self.root / "simulated-backup.db")
        with self.assertRaisesRegex(RuntimeError, "restore its backup"):
            open_state_store(self.settings)
        self.assertFalse(self.path.exists())

    def test_corrupt_database_is_rejected_before_manager_creation(self):
        self.path.parent.mkdir()
        raw = b"this is not an SQLite database" * 100
        self.path.write_bytes(raw)
        app = create_app(settings=self.settings)
        with mock.patch("facebook_backend.main._build_default_manager") as builder:
            with self.assertRaisesRegex(RuntimeError, "migrated SQLite schema"), TestClient(app):
                pass
            builder.assert_not_called()
        self.assertEqual(self.path.read_bytes(), raw)
        with state_owner(self.root):
            pass

    def test_head_alone_does_not_prove_schema_or_active_lease_constraint(self):
        _, database = self.bootstrap()
        with database.engine.begin() as conn:
            conn.exec_driver_sql("DROP INDEX uq_device_leases_active_device")
            conn.exec_driver_sql("CREATE UNIQUE INDEX uq_device_leases_active_device ON device_leases(device_id)")
        with self.assertRaisesRegex(RuntimeError, "lease constraint"):
            open_state_store(self.settings)
        with database.engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE artifacts")
        with self.assertRaisesRegex(RuntimeError, "core schema"):
            open_state_store(self.settings)

    def test_old_and_future_revisions_require_operator_action(self):
        _, database = self.bootstrap()
        for revision in ("0001_initial", "future_revision"):
            with database.engine.begin() as conn:
                conn.exec_driver_sql("UPDATE alembic_version SET version_num=?", (revision,))
            with self.subTest(revision=revision), self.assertRaisesRegex(RuntimeError, "Alembic head"):
                open_state_store(self.settings)
            with database.engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar(), revision)

    def test_each_legacy_directory_blocks_bootstrap_and_manifest_rollback_is_explicit(self):
        for directory, filename in (("api-jobs", "fb_old.job.json"), ("api-batches", "batch_old.batch.json")):
            root = self.root / directory
            path = root / directory / filename
            path.parent.mkdir(parents=True)
            path.write_bytes(b"partially written")
            settings = self.settings.with_overrides(base_dir=root)
            for mode in ("auto", "database"):
                with self.subTest(directory=directory, mode=mode), self.assertRaisesRegex(RuntimeError, "--verify"):
                    open_state_store(settings.with_overrides(job_store=mode))
                self.assertFalse(default_database_path(root).exists())
            store, db = open_state_store(settings.with_overrides(job_store="manifest"))
            self.assertIsInstance(store, ManifestJobStore)
            self.assertEqual(store.root, root)
            self.assertIsNone(db)
            self.assertEqual(path.read_bytes(), b"partially written")

    def test_cutover_needs_verified_snapshot_and_surviving_identities(self):
        store, database = self.bootstrap()
        old = ManifestJobStore(self.root)
        job = {"job_id": "fb_old", "status": "queued", "created_at": "2026-01-01"}
        old.save_job(job)
        store.save_job(job)
        for mode in ("auto", "database"):
            with self.assertRaisesRegex(RuntimeError, "cutover receipt"):
                open_state_store(self.settings.with_overrides(job_store=mode))
        digest = manifest_digest(self.root)
        with database.session() as session:
            write_cutover_receipt(session, self.root, digest)
            write_cutover_receipt(session, self.root, digest)
            count = session.scalar(select(func.count()).select_from(JobEvent).where(JobEvent.job_id.is_(None)))
            self.assertEqual(count, 1)
        store.save_job(dict(job, status="completed"))  # normal progression after the cutover
        self.bootstrap()
        path = old.job_path("fb_old")
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(RuntimeError, "stale"):
            open_state_store(self.settings)
        path.write_bytes(original)
        with database.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM jobs")
        with self.assertRaisesRegex(RuntimeError, "identities missing"):
            open_state_store(self.settings)

    def test_factory_and_injected_manager_leave_real_owner_and_files_untouched(self):
        for kwargs in ({"manager_factory": lambda: object()}, {"manager": object()}):
            with mock.patch("facebook_backend.main.state_owner", side_effect=AssertionError("real guard")), \
                    mock.patch("facebook_backend.main._open_state_store", side_effect=AssertionError("real store")):
                app = create_app(settings=self.settings, **kwargs)
                with TestClient(app):
                    self.assertIsNotNone(app.state.manager)
            self.assertFalse(self.path.parent.exists())

    def test_builder_failure_and_shutdown_failure_dispose_once_and_unlock(self):
        for failure in ("build", "shutdown"):
            db = mock.Mock(spec=Database)
            shutdown = mock.Mock(side_effect=TypeError("shutdown failure"))
            class Manager:
                def shutdown(self, wait_seconds):
                    shutdown(wait_seconds)
            with mock.patch("facebook_backend.main._open_state_store", return_value=(object(), db)), \
                    mock.patch("facebook_backend.main._build_default_manager", return_value=Manager(),
                               side_effect=ValueError("build failure") if failure == "build" else None):
                app = create_app(settings=self.settings)
                with self.assertRaisesRegex((TypeError, ValueError), failure), TestClient(app):
                    pass
            db.dispose.assert_called_once_with()
            self.assertEqual(shutdown.call_count, int(failure == "shutdown"))
            self.assertIsNone(app.state.database)
            self.assertIsNone(app.state.manager)
            with state_owner(self.root):
                pass

    def test_active_worker_retains_owner_and_database_until_actual_exit(self):
        finish = threading.Event()
        released = threading.Event()
        worker = threading.Thread(target=finish.wait, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(finish.set)
        class Manager:
            _worker = worker
            def shutdown(self, wait_seconds):
                return {"drained": False}
        database = mock.Mock(spec=Database)
        database.dispose.side_effect = released.set
        with mock.patch("facebook_backend.main._open_state_store", return_value=(object(), database)), \
                mock.patch("facebook_backend.main._build_default_manager", return_value=Manager()):
            with TestClient(create_app(settings=self.settings)):
                pass
            with self.assertRaisesRegex(RuntimeError, "owner lock"), state_owner(self.root):
                pass
            database.dispose.assert_not_called()
            finish.set()
            self.assertTrue(released.wait(5))
            # Disposal precedes unlock in the reaper's ExitStack. Join it so
            # the assertion checks the completed release rather than racing it.
            for thread in threading.enumerate():
                if thread.name == "state-owner-release":
                    thread.join(5)
            with state_owner(self.root):
                pass

    def test_another_process_cannot_own_same_root_or_shared_database(self):
        child = (
            'import sys; from pathlib import Path; '
            'from facebook_backend.persistence.state_startup import state_owner\n'
            'try:\n with state_owner(Path(sys.argv[1]), sys.argv[2]): pass\n'
            'except RuntimeError as exc:\n print(exc); sys.exit(17)\n'
        )
        def probe(root, url):
            return subprocess.run([sys.executable, "-B", "-c", child, str(root), url],
                                  capture_output=True, text=True, timeout=20, check=False)
        url = default_database_url(self.root)
        other = self.root / "second"
        with state_owner(self.root, url):
            for root, db_url in ((other, url), (self.root, default_database_url(other))):
                result = probe(root, db_url)
                self.assertEqual(result.returncode, 17, (result.stdout, result.stderr))
                self.assertIn("owner lock", result.stdout)
        result = probe(other, url)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))


if __name__ == "__main__":
    unittest.main()
