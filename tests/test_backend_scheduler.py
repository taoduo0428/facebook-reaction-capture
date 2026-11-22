"""Device-aware scheduler tests (Phase 4).

Everything here runs with a **mock executor** against a real SQLite database:
no emulator, no ADB, no capture process.  The tests are the evidence for the ten
isolation guarantees in the task, plus the failure-class rules (rate limit stays
on its device, device failure may fail over, timeout/cancel never retry).

Note the deliberate split: the scheduler is tested as a component.  It is not
yet the live dispatch path — ``docs/backend-modernization/PHASE-04-SCHEDULER.md``
records why and what the flip requires.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.executors.legacy import LegacyCaptureExecutor
from facebook_backend.executors.mock import MockExecutor, completed, failed
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import EventRepository, JobRepository
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.dispatcher import Dispatcher
from facebook_backend.scheduler.failures import FailureClass, RetryPolicy, classify_failure
from facebook_backend.scheduler.leases import (
    LeaseHeldError,
    LeaseManager,
    TakeoverEvidence,
)
from facebook_backend.scheduler.manager import Scheduler, SchedulerClosedError
from facebook_backend.scheduler.models import Device, WorkerAssignment, WorkerHealth
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


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'sched.db').as_posix()}")
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

    def events(self, job_id: str) -> list[dict]:
        with self.database.session() as session:
            return EventRepository(session).list_for_job(job_id)


class DispatcherTests(SchedulerTestCase):
    def devices(self, count: int = 1, **overrides) -> list[Device]:
        return [
            Device.from_worker_config(worker, **overrides) for worker in make_workers(count)
        ]

    def test_serial_default_takes_one_device(self):
        plan = Dispatcher(max_concurrent_jobs=1, clock=self.now).plan(
            self.devices(3), queue_depth=3, now=self.now()
        )
        self.assertEqual(len(plan.assignments), 1)

    def test_busy_device_is_skipped(self):
        devices = self.devices(2)
        devices[0] = Device.from_worker_config(make_workers(2)[0], current_job_id="fb_x")
        plan = Dispatcher(clock=self.now).plan(devices, queue_depth=1, now=self.now())
        self.assertEqual([d.device_id for d in plan.assignments], ["worker-01"])
        self.assertEqual(plan.blocked["worker-00"], "busy")

    def test_cooldown_device_is_skipped(self):
        devices = self.devices(2)
        devices[0] = Device.from_worker_config(
            make_workers(2)[0], cooldown_until=self.now() + 60
        )
        plan = Dispatcher(clock=self.now).plan(devices, queue_depth=1, now=self.now())
        self.assertEqual([d.device_id for d in plan.assignments], ["worker-01"])
        self.assertEqual(plan.blocked["worker-00"], "cooldown")

    def test_device_with_active_lease_is_skipped(self):
        devices = self.devices(2)
        plan = Dispatcher(clock=self.now).plan(
            devices,
            active_leases={"worker-00": {"lease_token": "x"}},
            queue_depth=1,
            now=self.now(),
        )
        self.assertEqual([d.device_id for d in plan.assignments], ["worker-01"])
        self.assertEqual(plan.blocked["worker-00"], "lease-held")

    def test_disabled_device_is_skipped(self):
        devices = self.devices(2)
        devices[0] = Device.from_worker_config(
            WorkerConfig("worker-00", "emulator-5554", "avd", 8081, 18766, False)
        )
        plan = Dispatcher(clock=self.now).plan(devices, queue_depth=1, now=self.now())
        self.assertEqual([d.device_id for d in plan.assignments], ["worker-01"])
        self.assertEqual(plan.blocked["worker-00"], "disabled")

    def test_resource_conflict_prevents_two_devices_with_the_same_port(self):
        base = make_workers(2)
        devices = [
            Device.from_worker_config(base[0]),
            Device.from_worker_config(
                WorkerConfig("worker-09", "emulator-9999", "avd-x", 8081, 18799, True)
            ),
        ]
        plan = Dispatcher(max_concurrent_jobs=2, clock=self.now).plan(
            devices, queue_depth=2, now=self.now()
        )
        self.assertEqual(len(plan.assignments), 1)
        self.assertTrue(plan.blocked["worker-09"].startswith("resource-conflict"))

    def test_unhealthy_device_is_only_skipped_when_required(self):
        base = make_workers(1)[0]
        device = Device.from_worker_config(base, health=WorkerHealth(base.worker_id, "offline", False))
        lenient = Dispatcher(clock=self.now).plan([device], queue_depth=1, now=self.now())
        strict = Dispatcher(require_healthy_device=True, clock=self.now).plan(
            [device], queue_depth=1, now=self.now()
        )
        self.assertEqual(len(lenient.assignments), 1)
        self.assertEqual(len(strict.assignments), 0)
        self.assertEqual(strict.blocked["worker-00"], "unhealthy")

    def test_empty_queue_dispatches_nothing(self):
        plan = Dispatcher(clock=self.now).plan(self.devices(2), queue_depth=0, now=self.now())
        self.assertEqual(plan.assignments, ())


class QueueTests(SchedulerTestCase):
    def setUp(self):
        super().setUp()
        self.queue = JobQueue(self.database, clock=self.now)

    def test_claim_moves_the_oldest_job_to_running(self):
        self.enqueue("fb_a", created_at="2026-01-01T00:00:00Z")
        self.enqueue("fb_b", created_at="2026-01-02T00:00:00Z")
        claimed = self.queue.claim(device_id="worker-00", worker_id="worker-00", device_serial="emulator-5554")
        self.assertEqual(claimed["id"], "fb_a")
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertEqual(claimed["assigned_worker_id"], "worker-00")
        self.assertEqual(self.queue.depth(), 1)

    def test_claim_skips_jobs_pinned_to_another_device(self):
        self.enqueue("fb_pinned", created_at="2026-01-01T00:00:00Z")
        self.enqueue("fb_free", created_at="2026-01-02T00:00:00Z")
        self.queue.pin("fb_pinned", "worker-01")
        claimed = self.queue.claim(device_id="worker-00", worker_id="worker-00", device_serial="x")
        self.assertEqual(claimed["id"], "fb_free")

    def test_pinned_job_is_claimable_by_its_device(self):
        self.enqueue("fb_pinned")
        self.queue.pin("fb_pinned", "worker-01")
        self.assertIsNone(self.queue.claim(device_id="worker-00", worker_id="worker-00", device_serial="x"))
        claimed = self.queue.claim(device_id="worker-01", worker_id="worker-01", device_serial="y")
        self.assertEqual(claimed["id"], "fb_pinned")

    def test_release_can_keep_or_clear_the_pin(self):
        self.enqueue("fb_a")
        self.queue.claim(device_id="worker-00", worker_id="worker-00", device_serial="x")
        self.queue.pin("fb_a", "worker-00")
        kept = self.queue.release("fb_a", reason="retry")
        self.assertEqual(kept["pinned_device_id"], "worker-00")
        cleared = self.queue.release("fb_a", reason="failover", pinned_device_id=None)
        self.assertIsNone(cleared["pinned_device_id"])

    def test_concurrent_claims_never_hand_out_the_same_job(self):
        for index in range(5):
            self.enqueue(f"fb_{index}")
        claimed: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                job = self.queue.claim(device_id="worker-00", worker_id="worker-00", device_serial="x")
                if job is None:
                    return
                with lock:
                    claimed.append(job["id"])

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(claimed), 5)
        self.assertEqual(len(set(claimed)), 5)


class LeaseManagerTests(SchedulerTestCase):
    def setUp(self):
        super().setUp()
        self.leases = LeaseManager(self.database, ttl_seconds=60, clock=self.now)
        with self.database.session() as session:
            from facebook_backend.persistence.repositories import DeviceRepository

            DeviceRepository(session).upsert(
                device_id="worker-00", worker_id="worker-00", serial="emulator-5554"
            )

    def test_second_acquire_is_refused_while_the_lease_is_valid(self):
        self.leases.acquire("worker-00", job_id="fb_a")
        with self.assertRaises(LeaseHeldError):
            self.leases.acquire("worker-00", job_id="fb_b")

    def test_expired_lease_without_evidence_is_not_taken_over(self):
        self.leases.acquire("worker-00", job_id="fb_a", ttl_seconds=-1)
        with self.assertRaises(LeaseHeldError) as caught:
            self.leases.acquire("worker-00", job_id="fb_b")
        self.assertIn("not verified stopped", str(caught.exception))
        with self.assertRaises(LeaseHeldError) as caught:
            self.leases.acquire(
                "worker-00", job_id="fb_b", evidence=TakeoverEvidence(process_stopped=True)
            )
        self.assertIn("cleanup", str(caught.exception))

    def test_expired_lease_with_full_evidence_is_taken_over(self):
        first = self.leases.acquire("worker-00", job_id="fb_a", ttl_seconds=-1)
        second = self.leases.acquire(
            "worker-00",
            job_id="fb_b",
            evidence=TakeoverEvidence(process_stopped=True, cleanup_confirmed=True),
        )
        self.assertEqual(second["fencing_version"], first["fencing_version"] + 1)
        self.assertIsNone(self.leases.active("worker-00")["released_at"])

    def test_heartbeat_extends_and_release_frees_the_device(self):
        lease = self.leases.acquire("worker-00", job_id="fb_a")
        beat = self.leases.heartbeat(lease["lease_token"])
        self.assertGreaterEqual(beat["expires_at"], lease["expires_at"])
        self.leases.release(lease["lease_token"], reason="completed")
        self.assertIsNone(self.leases.active("worker-00"))
        again = self.leases.acquire("worker-00", job_id="fb_b")
        self.assertEqual(again["fencing_version"], lease["fencing_version"] + 1)


class FailureClassificationTests(unittest.TestCase):
    def test_markers(self):
        self.assertIs(classify_failure("HTTP 429 rate limit"), FailureClass.RATE_LIMIT)
        self.assertIs(classify_failure("device offline"), FailureClass.DEVICE)
        self.assertIs(classify_failure("connection reset by peer"), FailureClass.NETWORK)
        self.assertIs(classify_failure("capture timed out after 600s"), FailureClass.TIMEOUT)
        self.assertIs(classify_failure("job cancelled by operator"), FailureClass.CANCELLED)
        self.assertIs(classify_failure("exit 2"), FailureClass.EXECUTOR)
        self.assertIs(classify_failure(None), FailureClass.UNKNOWN)

    def test_rate_limit_never_fails_over(self):
        decision = RetryPolicy().decide(FailureClass.RATE_LIMIT, attempt=1)
        self.assertTrue(decision.retry)
        self.assertTrue(decision.same_device)
        self.assertFalse(decision.failover_allowed)
        self.assertGreaterEqual(decision.cooldown_seconds, 900)

    def test_timeout_and_cancel_never_retry(self):
        policy = RetryPolicy()
        self.assertFalse(policy.decide(FailureClass.TIMEOUT, attempt=1).retry)
        self.assertFalse(policy.decide(FailureClass.CANCELLED, attempt=1).retry)

    def test_device_failure_fails_over_until_attempts_run_out(self):
        policy = RetryPolicy(max_attempts=2)
        first = policy.decide(FailureClass.DEVICE, attempt=1)
        self.assertTrue(first.retry)
        self.assertTrue(first.failover_allowed)
        self.assertFalse(policy.decide(FailureClass.DEVICE, attempt=2).retry)


class SchedulerTests(SchedulerTestCase):
    def build(self, *, devices: int = 1, executor=None, max_concurrent_jobs: int = 1, **kwargs):
        return Scheduler(
            database=self.database,
            devices=make_workers(devices),
            executor=executor or MockExecutor(),
            max_concurrent_jobs=max_concurrent_jobs,
            clock=self.now,
            **kwargs,
        )

    def advance(self, seconds: float) -> None:
        self.clock_value += seconds

    def test_serial_scheduler_runs_one_job_per_tick(self):
        self.enqueue("fb_a")
        self.enqueue("fb_b")
        executor = MockExecutor()
        scheduler = self.build(devices=1, executor=executor)
        first = scheduler.tick()
        self.assertEqual([o.job_id for o in first], ["fb_a"])
        self.assertEqual(self.job("fb_a")["status"], "completed")
        self.assertEqual(self.job("fb_b")["status"], "queued")
        second = scheduler.tick()
        self.assertEqual([o.job_id for o in second], ["fb_b"])

    def test_one_job_per_device_and_devices_run_independently(self):
        self.enqueue("fb_a")
        self.enqueue("fb_b")
        gate = threading.Event()
        executor = MockExecutor(gate=gate)
        scheduler = self.build(devices=2, executor=executor, max_concurrent_jobs=2)

        results: dict[str, ExecutionOutcome] = {}

        def run() -> None:
            for outcome in scheduler.tick(parallel=True):
                results[outcome.job_id] = outcome

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while len(executor.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        gate.set()
        thread.join(timeout=10)
        self.assertEqual(set(results), {"fb_a", "fb_b"})
        devices_used = executor.devices_used()
        self.assertEqual(len(devices_used), 2, executor.calls)
        self.assertEqual(self.job("fb_a")["status"], "completed")
        self.assertEqual(self.job("fb_b")["status"], "completed")

    def test_rate_limit_keeps_the_job_on_its_device(self):
        self.enqueue("fb_a")
        self.enqueue("fb_b")
        executor = MockExecutor(
            script={
                "fb_a": failed("fb_a", FailureClass.RATE_LIMIT, "HTTP 429 rate limit"),
                "fb_b": completed("fb_b"),
            }
        )
        scheduler = self.build(devices=2, executor=executor, max_concurrent_jobs=1)
        scheduler.tick()
        job = self.job("fb_a")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["pinned_device_id"], "worker-00")
        self.assertEqual(job["recovery_state"], "retry-rate-limit")
        calls_after_first_tick = len(executor.calls)
        # The other device must not pick the pinned job up.
        self.advance(10)
        outcome = scheduler.tick()
        self.assertEqual([o.job_id for o in outcome], ["fb_b"])
        later_calls = [job_id for _device, job_id in executor.calls[calls_after_first_tick:]]
        self.assertEqual(later_calls, ["fb_b"])

    def test_rate_limited_job_resumes_on_the_same_device_after_cooldown(self):
        self.enqueue("fb_a")
        attempts = {"count": 0}

        def script(assignment: WorkerAssignment) -> ExecutionOutcome:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return failed("fb_a", FailureClass.RATE_LIMIT, "HTTP 429 rate limit")
            return completed("fb_a")

        scheduler = self.build(devices=2, executor=MockExecutor(script={"fb_a": script}))
        scheduler.tick()
        self.assertEqual(self.job("fb_a")["status"], "queued")
        self.advance(1000)
        scheduler.tick()
        final = self.job("fb_a")
        self.assertEqual(final["status"], "completed")
        self.assertIsNone(final["pinned_device_id"])

    def test_device_failure_fails_over_to_another_device(self):
        self.enqueue("fb_a")
        scheduler = self.build(
            devices=2,
            executor=MockExecutor(
                script={"fb_a": failed("fb_a", FailureClass.DEVICE, "device offline")}
            ),
        )
        scheduler.tick()
        self.assertEqual(self.job("fb_a")["status"], "queued")
        self.assertIsNone(self.job("fb_a")["pinned_device_id"])
        self.advance(1000)
        scheduler.executor = MockExecutor()
        scheduler.tick()
        self.assertEqual(self.job("fb_a")["status"], "completed")

    def test_timeout_fails_without_retry(self):
        self.enqueue("fb_a")
        scheduler = self.build(
            devices=1,
            executor=MockExecutor(
                script={"fb_a": failed("fb_a", FailureClass.TIMEOUT, "capture timed out after 600s")}
            ),
        )
        scheduler.tick()
        job = self.job("fb_a")
        self.assertEqual(job["status"], "failed")
        self.assertIn("timed out", job["error_message"])

    def test_cancelled_job_is_terminal_and_not_retried(self):
        self.enqueue("fb_a")
        scheduler = self.build(
            devices=1,
            executor=MockExecutor(
                script={"fb_a": ExecutionOutcome(job_id="fb_a", status="cancelled", process_state="stopped")}
            ),
        )
        scheduler.tick()
        self.assertEqual(self.job("fb_a")["status"], "cancelled")
        self.assertIsNone(self.job("fb_a")["pinned_device_id"])

    def test_executor_crash_is_classified_and_retried(self):
        self.enqueue("fb_a")

        class CrashingExecutor:
            def __init__(self):
                self.calls = 0

            def execute(self, assignment, *, cancel_event=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("runner crashed")
                return completed(assignment.job_id)

        executor = CrashingExecutor()
        scheduler = self.build(devices=1, executor=executor)
        first = scheduler.tick()
        self.assertEqual(first[0].failure, FailureClass.EXECUTOR)
        self.assertEqual(self.job("fb_a")["status"], "queued")
        self.advance(1000)
        scheduler.tick()
        self.assertEqual(self.job("fb_a")["status"], "completed")

    def test_one_device_failure_does_not_affect_the_other(self):
        self.enqueue("fb_a", created_at="2026-01-01T00:00:00Z")
        self.enqueue("fb_b", created_at="2026-01-02T00:00:00Z")

        class SplitExecutor:
            def execute(self, assignment, *, cancel_event=None):
                if assignment.device.device_id == "worker-00":
                    raise RuntimeError("device exploded")
                return completed(assignment.job_id)

        scheduler = self.build(devices=2, executor=SplitExecutor(), max_concurrent_jobs=2)
        outcomes = {o.job_id: o for o in scheduler.tick(parallel=True)}
        self.assertEqual(outcomes["fb_a"].status, "failed")
        self.assertEqual(outcomes["fb_b"].status, "completed")
        self.assertEqual(self.job("fb_b")["status"], "completed")
        self.assertEqual(self.job("fb_a")["status"], "queued")

    def test_shutdown_stops_accepting_work(self):
        scheduler = self.build(devices=1)
        scheduler.shutdown()
        self.assertTrue(scheduler.closed)
        with self.assertRaises(SchedulerClosedError):
            scheduler.tick()

    def test_snapshot_reports_devices_slots_and_queue_depth(self):
        self.enqueue("fb_a")
        scheduler = self.build(devices=2)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot.max_concurrent_jobs, 1)
        self.assertEqual(snapshot.queue_depth, 1)
        self.assertEqual(len(snapshot.devices), 2)
        self.assertEqual(snapshot.slots, ())
        scheduler.tick()
        self.assertEqual(snapshot.to_dict()["queue_depth"], 1)

    def test_events_record_dispatch_and_completion(self):
        self.enqueue("fb_a")
        scheduler = self.build(devices=1)
        scheduler.tick()
        event_types = [event["event_type"] for event in self.events("fb_a")]
        self.assertEqual(event_types, ["dispatched", "completed"])

    def test_lease_is_released_after_each_job(self):
        self.enqueue("fb_a")
        scheduler = self.build(devices=1)
        scheduler.tick()
        self.assertEqual(scheduler.leases.active_leases(), [])

    def test_malformed_payload_releases_the_lease_and_requeues(self):
        with self.database.session() as session:
            JobRepository(session).create(
                job_id="fb_bad",
                status="queued",
                payload={"request": "not-a-dict"},
                run_dir="runs/fb_bad",
            )
        executor = MockExecutor()
        scheduler = self.build(devices=1, executor=executor)
        outcome = scheduler.tick()
        self.assertEqual(outcome[0].status, "failed")
        self.assertEqual(executor.calls, [])
        self.assertEqual(scheduler.leases.active_leases(), [])
        self.assertEqual(self.job("fb_bad")["status"], "queued")

    def test_second_tick_does_not_restart_a_running_job(self):
        self.enqueue("fb_a")
        gate = threading.Event()
        executor = MockExecutor(gate=gate)
        scheduler = self.build(devices=1, executor=executor)
        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        deadline = time.monotonic() + 5
        while not executor.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        # While the first job is in flight the device holds a slot and a lease.
        second = scheduler.tick()
        self.assertEqual(second, [])
        gate.set()
        thread.join(timeout=10)
        self.assertEqual(self.job("fb_a")["status"], "completed")


class LegacyExecutorTests(SchedulerTestCase):
    def assignment(self) -> WorkerAssignment:
        device = Device.from_worker_config(make_workers(1)[0])
        return WorkerAssignment(job_id="fb_a", device=device, request={"post_url": "x"}, run_dir="runs/fb_a")

    def test_success_is_completed_with_a_verified_stop(self):
        executor = LegacyCaptureExecutor(lambda *a, **k: {"job_id": "fb_a", "reaction_users": 3})
        outcome = executor.execute(self.assignment())
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.process_state, "stopped")
        self.assertEqual(outcome.result["reaction_users"], 3)

    def test_failure_is_failed_without_claiming_a_stop(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("capture command failed with exit 2")

        outcome = LegacyCaptureExecutor(boom).execute(self.assignment())
        self.assertEqual(outcome.status, "failed")
        self.assertIsNone(outcome.process_state)
        self.assertIs(outcome.failure, FailureClass.EXECUTOR)

    def test_rate_limit_message_is_classified(self):
        def limited(*_args, **_kwargs):
            raise RuntimeError("HTTP 429 rate limit exceeded")

        outcome = LegacyCaptureExecutor(limited).execute(self.assignment())
        self.assertIs(outcome.failure, FailureClass.RATE_LIMIT)


if __name__ == "__main__":
    unittest.main()
