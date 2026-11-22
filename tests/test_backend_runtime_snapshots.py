"""Runtime GET contract. All state lives in a temporary, synthetic WAL database."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from facebook_backend.config import Settings
from facebook_backend.main import create_app
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import Device, DeviceLease, Job, JobAttempt


WORKERS = "/v1/workers"
DEVICES = "/v1/devices"
SCHEDULER = "/v1/scheduler"
HISTORY = "/v1/workers/worker-a/history"
LEASES = "/v1/leases/history"
PATHS = (WORKERS, DEVICES, SCHEDULER, HISTORY, LEASES)
T1 = "2024-01-01T00:00:00Z"
T2 = "2024-01-02T00:00:00Z"
T3 = "2024-01-03T00:00:00Z"
PRIVATE = "https://fixture.invalid/private?token=fixture"


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "projection #1 %.sqlite"
        self.db = stack.enter_context(Database.open(f"sqlite:///{self.path.as_posix()}"))
        self.db.create_all()
        settings = Settings(base_dir=self.root, load_local_config=False)
        # Exercise the production HTTP factory and middleware without an executor.
        self.app = create_app(manager=object(), settings=settings)
        self.client = stack.enter_context(TestClient(self.app, base_url="http://127.0.0.1:8765"))
        self.app.state.database = self.db
        stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("poll spawned a process")))
        stack.enter_context(patch("facebook_capture_api.adb_snapshot", side_effect=AssertionError("poll probed ADB")))

    def seed(self):
        with self.db.session() as s:
            for worker, health in (("a", "offline"), ("b", None)):
                s.add(Device(id="dev-" + worker, worker_id="worker-" + worker, serial="private-hardware-" + worker,
                             enabled=1, health_status=health, capabilities=PRIVATE, avd_name="private-account",
                             updated_at=T1, current_job_id="fb_one" if worker == "a" else None))
            for job_id, status in (("fb_one", "running"), ("fb_two", "queued")):
                s.add(Job(id=job_id, status=status, created_at=T1, updated_at=T2, payload=PRIVATE,
                          result=PRIVATE, error_message=PRIVATE, run_dir=PRIVATE))
            s.flush()
            for index, worker, stamp in ((1, "a", T1), (2, "b", T2), (3, "a", T3)):
                s.add(JobAttempt(attempt_id=f"att_{index}", job_id="fb_one", attempt_index=index,
                                 worker_id="worker-" + worker, started_at=stamp, result_status="failed" if index < 3 else None,
                                 run_dir=PRIVATE, device_serial="private-hardware"))
            for index, released, heartbeat in ((1, T2, T2), (2, None, T3)):
                s.add(DeviceLease(device_id="dev-a", worker_id="worker-a", job_id="fb_one",
                                  lease_token=f"fixture-secret-{index}", fencing_version=index,
                                  acquired_at=T1 if released else T3, heartbeat_at=heartbeat, expires_at=T3,
                                  released_at=released, release_reason=PRIVATE if released else None))

    def test_production_claim_and_finish_are_visible_without_polling_manager(self):
        from facebook_backend.executors.base import ExecutionOutcome
        from facebook_backend.persistence.job_store import DatabaseJobStore
        from facebook_backend.scheduler.device_registry import WorkerConfig
        from facebook_backend.scheduler.production_transactions import ProductionTransactions

        store = DatabaseJobStore(self.db)
        store.accept_job({"job_id": "fb_projection", "status": "queued", "created_at": T1,
                          "request": {"post_url": PRIVATE}})
        transactions = ProductionTransactions(store, self.root)
        worker = WorkerConfig("worker-a", "fixture-serial", "fixture-avd", 18080, 18081, True)
        claim = transactions.claim(worker)
        self.assertEqual(self.client.get(SCHEDULER).json()["active_leases"], 1)
        self.assertEqual(self.client.get(HISTORY).json()["attempts"][0]["attempt_id"], claim.attempt_id)
        self.assertEqual(self.client.get(DEVICES).json()["devices"][0]["health_status"], "unknown")
        self.assertIsNone(self.client.get(LEASES).json()["leases"][0]["heartbeat_at"])
        transactions.finish(claim, ExecutionOutcome(job_id="fb_projection", attempt_id=claim.attempt_id,
                            status="failed", process_state="not-started", transport_cleanup_state="not-required"))
        self.assertEqual(self.client.get(SCHEDULER).json()["active_leases"], 0)
        self.assertEqual(self.client.get(HISTORY).json()["attempts"][0]["result_status"], "failed")
        self.assertEqual(self.client.get(LEASES).json()["leases"][0]["state"], "released")

    def test_runtime_route_inventory_is_additive_and_get_only(self):
        expected = {WORKERS, DEVICES, SCHEDULER, "/v1/workers/{worker_id}/history", LEASES}
        runtime_routes = [route for route in self.app.routes if "runtime" in getattr(route, "tags", [])]
        self.assertEqual({route.path for route in runtime_routes}, expected)
        for route in runtime_routes:
            self.assertEqual(route.methods, {"GET"})

    def test_device_worker_scheduler_and_lease_facts(self):
        self.seed()
        devices = self.client.get(DEVICES).json()["devices"]
        self.assertEqual([d["health_status"] for d in devices], ["offline", "unknown"])
        self.assertEqual([d["active_lease_id"] for d in devices], [2, None])
        self.assertTrue(all(d["last_heartbeat_at"] is None for d in devices))
        workers = self.client.get(WORKERS).json()["workers"]
        self.assertEqual([w["enabled"] for w in workers], [True, True])
        self.assertTrue(all(w["health_status"] == "unknown" and w["last_heartbeat_at"] is None for w in workers))
        self.assertEqual([(w["worker_id"], w["current_job_ids"]) for w in workers],
                         [("worker-a", ["fb_one"]), ("worker-b", [])])
        scheduler = self.client.get(SCHEDULER).json()
        self.assertEqual(scheduler["max_concurrent_jobs"], 1)
        self.assertEqual(scheduler["mode"], "primary-standby-serial")
        self.assertEqual((scheduler["state"], scheduler["last_heartbeat_at"]), ("unknown", None))
        self.assertEqual((scheduler["queue_depth"], scheduler["running_jobs"], scheduler["active_leases"]), (1, 1, 1))
        leases = self.client.get(LEASES).json()["leases"]
        self.assertEqual([(l["lease_id"], l["state"], l["heartbeat_at"]) for l in leases],
                         [(2, "held", None), (1, "released", T2)])
        self.assertEqual(leases[1]["release_reason"], "unknown")

    def test_histories_are_bounded_deterministic_and_worker_scoped(self):
        self.seed()
        attempts = self.client.get(HISTORY).json()["attempts"]
        self.assertEqual([r["attempt_index"] for r in attempts], [3, 1])
        self.assertEqual([r["result_status"] for r in attempts], [None, "failed"])
        self.assertEqual(self.client.get(HISTORY, params={"limit": 1}).json()["attempts"], attempts[:1])
        self.assertEqual(len(self.client.get(LEASES + "?limit=1").json()["leases"]), 1)
        for path in (HISTORY, LEASES):
            for limit in (0, 101, "invalid"):
                with self.subTest(path=path, limit=limit):
                    self.assertEqual(self.client.get(path, params={"limit": limit}).status_code, 400)
        self.assertEqual(self.client.get("/v1/workers/absent/history").status_code, 404)
        self.assertEqual(self.client.get("/v1/workers/bad%40id/history").status_code, 400)

    def test_empty_history_and_empty_registered_inventory(self):
        self.assertEqual(self.client.get(WORKERS).json(), {"workers": []})
        self.assertEqual(self.client.get(DEVICES).json(), {"devices": []})
        self.assertEqual(self.client.get(LEASES).json(), {"leases": []})
        self.seed()
        with self.db.session() as s:
            s.add(Device(id="dev-c", serial="fixture-c", worker_id="worker-c", enabled=0, updated_at=T1))
        self.assertEqual(self.client.get("/v1/workers/worker-c/history").json(),
                         {"worker_id": "worker-c", "attempts": []})

    def test_polling_reads_only_public_columns_and_never_changes_database(self):
        self.seed()
        connect = sqlite3.connect
        private_columns = {"lease_token", "payload", "result", "error_message", "serial", "device_serial",
                           "run_dir", "process_id", "capabilities", "avd_name"}
        with closing(connect(self.path)) as sentinel:
            initial_version = sentinel.execute("PRAGMA data_version").fetchone()

            def guarded_connect(uri, /, **kw):
                self.assertTrue(uri.endswith("?mode=ro"))
                conn = connect(uri, **kw)

                def allowed(action, table, column, database, trigger):
                    if action == sqlite3.SQLITE_READ and column in private_columns:
                        return sqlite3.SQLITE_DENY
                    if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_FUNCTION):
                        return sqlite3.SQLITE_OK
                    return sqlite3.SQLITE_DENY

                conn.set_authorizer(allowed)
                return conn

            with patch("facebook_backend.services.runtime_snapshot.sqlite3.connect", side_effect=guarded_connect):
                for path in PATHS:
                    with self.subTest(path=path):
                        first = self.client.get(path)
                        self.assertEqual(first.status_code, 200, first.text)
                        self.assertEqual(first.headers["cache-control"], "no-store")
                        self.assertEqual(self.client.get(path).content, first.content)
                        for value in (PRIVATE, "lease_token", "fixture-secret", "private-hardware", "private-account"):
                            self.assertNotIn(value, first.text)
                        self.assertEqual(self.client.post(path).status_code, 404)
            self.assertEqual(sentinel.execute("PRAGMA data_version").fetchone(), initial_version)

    def test_pending_writes_are_invisible_then_visible_after_commit(self):
        self.seed()
        with self.db.engine.connect() as writer:
            writer.exec_driver_sql("UPDATE jobs SET status='cancelling' WHERE id='fb_one'")
            first = self.client.get(SCHEDULER).json()
            self.assertEqual((first["running_jobs"], first["cancelling_jobs"]), (1, 0))
            writer.commit()
        second = self.client.get(SCHEDULER).json()
        self.assertEqual((second["running_jobs"], second["cancelling_jobs"]), (0, 1))

    def test_scheduler_counts_share_one_snapshot_during_concurrent_commit(self):
        self.seed()
        connect = sqlite3.connect
        with closing(connect(self.path, check_same_thread=False)) as writer:
            def reader_connect(uri, /, **kw):
                conn = connect(uri, **kw)

                def interleave(sql):
                    if "FROM device_leases" in sql:
                        writer.execute("UPDATE jobs SET status='completed' WHERE id='fb_one'")
                        writer.execute("UPDATE device_leases SET released_at=? WHERE released_at IS NULL", (T3,))
                        writer.commit()

                conn.set_trace_callback(interleave)
                return conn

            with patch("facebook_backend.services.runtime_snapshot.sqlite3.connect", side_effect=reader_connect):
                snapshot = self.client.get(SCHEDULER).json()
            self.assertEqual((snapshot["running_jobs"], snapshot["active_leases"]), (1, 1))
            after = self.client.get(SCHEDULER).json()
            self.assertEqual((after["running_jobs"], after["active_leases"]), (0, 0))

    def test_unavailable_projection_never_bootstraps_or_leaks_path(self):
        self.app.state.database = None
        for path in PATHS:
            self.assertEqual(self.client.get(path).status_code, 503)
        missing = self.root / "missing.sqlite"
        with Database.open(f"sqlite:///{missing.as_posix()}") as uninitialized:
            self.app.state.database = uninitialized
            for path in PATHS:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": "SQLite runtime projection unavailable"})
            self.assertFalse(missing.exists())
