"""Reliability tests (Phase 5).

Covers the lifecycle guarantees the task calls out explicitly:

* shutdown stops accepting new work and never fabricates a "stopped" process,
* a repeated submission with the same ``Idempotency-Key`` returns the original
  job (including after a restart),
* the capture runtime ceiling is actually passed to the runner,
* cooperative cancellation only becomes terminal when the stop is proven,
* startup recovery requeues only what it can prove is gone, and releases a device
  lease only with the same proof.

The scheduler/legacy-manager tests use a real SQLite database and a mock
executor; no emulator, no ADB, no capture process.
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import facebook_capture_api as api
from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.executors.mock import MockExecutor, completed, failed
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import (
    DeviceRepository,
    JobRepository,
    LeaseRepository,
)
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.leases import LeaseManager
from facebook_backend.scheduler.manager import Scheduler
from facebook_backend.scheduler.recovery import (
    ACTION_BLOCK,
    ACTION_RELEASE_LEASE,
    ACTION_REQUEUE,
    RecoveryPlanner,
)
from facebook_backend.scheduler.queue import JobQueue


def make_workers(count: int = 1) -> list[WorkerConfig]:
    return [
        WorkerConfig(
            worker_id=f"worker-{index:02d}",
            serial=f"emulator-{5554 + index * 2}",
            avd_name=f"avd-{index:02d}",
            capture_port=8081 + index,
            native_port=18766 + index,
            enabled=True,
        )
        for index in range(count)
    ]


class LegacyManagerReliabilityTests(unittest.TestCase):
    """The live manager's shutdown / idempotency / runtime ceiling."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        base = mock.patch.object(api, "BASE_DIR", self.root)
        base.start()
        self.addCleanup(base.stop)
        # Do not start the scheduler thread: these tests exercise the API-side
        # lifecycle, not dispatch (that needs a device).
        starter = mock.patch.object(
            api.JobManager, "_start_worker", lambda self, worker_id: None
        )
        starter.start()
        self.addCleanup(starter.stop)
        self.manager = api.JobManager("emulator-5554")

    def payload(self) -> dict:
        return {"post_url": "https://www.facebook.com/x/posts/1"}

    def test_submit_creates_a_queued_job(self):
        job = self.manager.submit(self.payload())
        self.assertEqual(job["status"], "queued")
        self.assertEqual(self.manager.health()["queued_jobs"], 1)

    def test_shutdown_refuses_new_jobs(self):
        result = self.manager.shutdown(wait_seconds=0)
        self.assertFalse(result["accepting_submissions"])
        self.assertFalse(self.manager.accepting_submissions)
        with self.assertRaises(api.ApiError) as caught:
            self.manager.submit(self.payload())
        self.assertIn("shutting down", str(caught.exception))
        with self.assertRaises(api.ApiError):
            self.manager.submit_batch({"post_urls": ["https://www.facebook.com/x/posts/1"]})

    def test_shutdown_does_not_cancel_or_claim_anything(self):
        job = self.manager.submit(self.payload())
        self.manager.shutdown(wait_seconds=0)
        stored = self.manager.public_job(job["job_id"])
        self.assertEqual(stored["status"], "queued")
        self.assertIsNone(stored["process_state"])

    def test_shutdown_reports_an_active_job_without_inventing_a_stop(self):
        job = self.manager.submit(self.payload())
        with self.manager._lock:
            stored = self.manager._jobs[job["job_id"]]
            stored["status"] = "running"
            stored["process_state"] = "running"
            self.manager._active_job_id = job["job_id"]
        result = self.manager.shutdown(wait_seconds=0.05)
        self.assertFalse(result["drained"])
        self.assertEqual(result["active_job_id"], job["job_id"])
        self.assertEqual(result["active_status"], "running")
        self.assertEqual(result["process_state"], "running")
        self.assertIn("no capture process was terminated", result["note"])

    def test_shutdown_waits_for_the_job_to_finish(self):
        job = self.manager.submit(self.payload())
        with self.manager._lock:
            self.manager._jobs[job["job_id"]]["status"] = "running"
            self.manager._active_job_id = job["job_id"]

        def finish() -> None:
            time.sleep(0.15)
            with self.manager._lock:
                self.manager._jobs[job["job_id"]]["status"] = "completed"
                self.manager._active_job_id = None

        thread = threading.Thread(target=finish)
        thread.start()
        result = self.manager.shutdown(wait_seconds=2)
        thread.join(timeout=5)
        self.assertTrue(result["drained"])

    def test_idempotency_key_returns_the_same_job(self):
        first = self.manager.submit(self.payload(), submission_key="req-1")
        second = self.manager.submit(self.payload(), submission_key="req-1")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.manager.health()["queued_jobs"], 1)
        other = self.manager.submit(self.payload(), submission_key="req-2")
        self.assertNotEqual(other["job_id"], first["job_id"])

    def test_idempotency_key_survives_a_restart(self):
        first = self.manager.submit(self.payload(), submission_key="req-restart")
        restarted = api.JobManager("emulator-5554")
        second = restarted.submit(self.payload(), submission_key="req-restart")
        self.assertEqual(first["job_id"], second["job_id"])

    def test_runtime_ceiling_is_passed_to_the_runner(self):
        manager = api.JobManager("emulator-5554", max_runtime_seconds=42)
        worker = make_workers(1)[0]
        with mock.patch.object(api, "run_capture_job", return_value={"job_id": "fb_x"}) as runner:
            manager._execute_capture("fb_x", self.payload(), Path("runs/fb_x"), worker)
        self.assertEqual(runner.call_args.kwargs["timeout_seconds"], 42)

    def test_zero_runtime_ceiling_keeps_the_historical_unlimited_wait(self):
        manager = api.JobManager("emulator-5554", max_runtime_seconds=0)
        worker = make_workers(1)[0]
        with mock.patch.object(api, "run_capture_job", return_value={"job_id": "fb_x"}) as runner:
            manager._execute_capture("fb_x", self.payload(), Path("runs/fb_x"), worker)
        self.assertIsNone(runner.call_args.kwargs["timeout_seconds"])

    def test_default_runtime_ceiling_is_documented_and_bounded(self):
        self.assertEqual(api.DEFAULT_MAX_RUNTIME_SECONDS, 6 * 60 * 60)
        self.assertEqual(api.JobManager("emulator-5554").max_runtime_seconds, 0)


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'r.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)
        self.clock_value = 1_700_000_000.0

    def now(self) -> float:
        return self.clock_value

    def enqueue(self, job_id: str, *, status: str = "queued", **fields) -> dict:
        with self.database.session() as session:
            return JobRepository(session).create(
                job_id=job_id,
                status=status,
                payload={"request": {"post_url": "https://www.facebook.com/x/posts/1"}},
                run_dir=f"runs/{job_id}",
                **fields,
            )

    def job(self, job_id: str) -> dict:
        with self.database.session() as session:
            return JobRepository(session).require(job_id)

    def build(self, *, devices: int = 1, executor=None, **kwargs) -> Scheduler:
        return Scheduler(
            database=self.database,
            devices=make_workers(devices),
            executor=executor or MockExecutor(),
            clock=self.now,
            **kwargs,
        )


class CooperativeCancellationTests(SchedulerTestCase):
    def test_cancel_a_queued_job_is_immediate(self):
        self.enqueue("fb_a")
        scheduler = self.build()
        result = scheduler.request_cancel("fb_a")
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["process_state"])  # never claims a stop

    def test_cancel_a_running_job_only_completes_after_a_verified_stop(self):
        self.enqueue("fb_a")
        started = threading.Event()

        class CancellableExecutor:
            def execute(self, assignment, *, cancel_event=None):
                started.set()
                cancel_event.wait(timeout=5)
                return ExecutionOutcome(
                    job_id=assignment.job_id, status="cancelled", process_state="stopped"
                )

        scheduler = self.build(executor=CancellableExecutor())
        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        pending = scheduler.request_cancel("fb_a")
        self.assertEqual(pending["status"], "cancelling")
        thread.join(timeout=10)
        self.assertEqual(self.job("fb_a")["status"], "cancelled")

    def test_cancel_that_cannot_be_proven_stays_cancelling(self):
        self.enqueue("fb_a")
        started = threading.Event()

        class StubbornExecutor:
            def execute(self, assignment, *, cancel_event=None):
                started.set()
                cancel_event.wait(timeout=5)
                return failed(
                    assignment.job_id, FailureClass.EXECUTOR, "process did not stop", process_state=None
                )

        scheduler = self.build(executor=StubbornExecutor())
        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        scheduler.request_cancel("fb_a")
        thread.join(timeout=10)
        job = self.job("fb_a")
        self.assertEqual(job["status"], "cancelling")
        self.assertEqual(job["recovery_state"], "cancel-failed-stop-unverified")

    def test_cancel_unknown_job_raises(self):
        scheduler = self.build()
        with self.assertRaises(KeyError):
            scheduler.request_cancel("fb_missing")


class RecoveryTests(SchedulerTestCase):
    def planner(self, identity: str) -> RecoveryPlanner:
        return RecoveryPlanner(
            self.database, identity_probe=lambda pid, created: identity, clock=self.now
        )

    def test_running_job_with_a_gone_process_is_requeued(self):
        self.enqueue("fb_a", status="running", process_pid=4242, process_creation_time=1.0)
        report = self.planner("absent").run()
        self.assertIn("fb_a", report.requeued)
        self.assertEqual(self.job("fb_a")["status"], "queued")
        self.assertEqual(self.job("fb_a")["recovery_state"], "requeued-after-restart")

    def test_running_job_with_a_live_process_stays_blocked(self):
        self.enqueue("fb_a", status="running", process_pid=4242, process_creation_time=1.0)
        report = self.planner("match").run()
        self.assertIn("fb_a", report.blocked)
        job = self.job("fb_a")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["recovery_state"], "restart-process-still-running")

    def test_unverified_identity_keeps_the_job_blocked(self):
        self.enqueue("fb_a", status="running", process_pid=4242)
        report = self.planner("unknown").run()
        self.assertIn("fb_a", report.blocked)
        self.assertEqual(self.job("fb_a")["recovery_state"], "restart-unverified")

    def test_cancelling_job_becomes_terminal_only_when_stopped(self):
        self.enqueue("fb_a", status="cancelling", process_pid=4242, process_creation_time=1.0)
        report = self.planner("absent").run()
        self.assertIn("fb_a", report.cancelled)
        job = self.job("fb_a")
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["process_state"], "stopped")

    def test_lease_of_a_finished_job_is_released(self):
        self.enqueue("fb_done", status="completed")
        with self.database.session() as session:
            DeviceRepository(session).upsert(device_id="worker-00", worker_id="worker-00", serial="e-1")
            LeaseRepository(session).acquire(device_id="worker-00", job_id="fb_done")
        report = self.planner("absent").run()
        self.assertEqual(len(report.released_leases), 1)
        self.assertEqual(LeaseManager(self.database).active_leases(), [])

    def test_lease_with_a_live_process_is_kept(self):
        self.enqueue("fb_run", status="running", process_pid=4242, process_creation_time=1.0)
        with self.database.session() as session:
            DeviceRepository(session).upsert(device_id="worker-00", worker_id="worker-00", serial="e-1")
            LeaseRepository(session).acquire(device_id="worker-00", job_id="fb_run")
        report = self.planner("match").run()
        self.assertEqual(report.released_leases, ())
        self.assertEqual(len(LeaseManager(self.database).active_leases()), 1)
        actions = [a for a in report.actions if a.action == ACTION_BLOCK and a.lease_token]
        self.assertTrue(actions)

    def test_recovery_is_idempotent(self):
        self.enqueue("fb_a", status="running", process_pid=4242, process_creation_time=1.0)
        planner = self.planner("absent")
        planner.run()
        second = planner.plan()
        self.assertEqual(second.requeued, ())
        self.assertEqual(second.blocked, ())

    def test_plan_is_read_only(self):
        self.enqueue("fb_a", status="running", process_pid=4242, process_creation_time=1.0)
        report = self.planner("absent").plan()
        self.assertIn("fb_a", report.requeued)
        self.assertFalse(report.applied)
        self.assertEqual(self.job("fb_a")["status"], "running")


class LifespanDrainTests(unittest.TestCase):
    def test_lifespan_calls_shutdown_with_the_configured_wait(self):
        from fastapi.testclient import TestClient

        from facebook_backend.config import Settings
        from facebook_backend.main import create_app

        calls: list[float] = []

        class Manager:
            accepting_submissions = True

            def health(self):
                return {"service": "ready"}

            def shutdown(self, wait_seconds=0.0):
                calls.append(wait_seconds)
                return {"drained": True}

        app = create_app(
            manager_factory=lambda: Manager(),
            settings=Settings(load_local_config=False, shutdown_wait_seconds=7.5),
        )
        with TestClient(app, base_url="http://127.0.0.1:8765"):
            pass
        self.assertEqual(calls, [7.5])

    def test_lifespan_tolerates_a_manager_without_shutdown(self):
        from fastapi.testclient import TestClient

        from facebook_backend.config import Settings
        from facebook_backend.main import create_app

        class Manager:
            accepting_submissions = True

            def health(self):
                return {"service": "ready"}

        app = create_app(
            manager_factory=lambda: Manager(), settings=Settings(load_local_config=False)
        )
        with TestClient(app, base_url="http://127.0.0.1:8765"):
            pass  # must not raise


if __name__ == "__main__":
    unittest.main()
