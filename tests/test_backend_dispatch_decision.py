"""Live dispatch decisions come from the scheduler's rule engine.

The worker loop no longer decides "may this device run?" with an inline
boolean: it asks ``Dispatcher.plan``, the same tested engine the multi-process
``Scheduler.tick()`` loop uses.  These tests pin the mapping from the manager's
runtime state to that decision, so the handoff cannot drift.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import facebook_capture_api as api
from facebook_backend.scheduler.device_registry import WorkerConfig


def workers() -> list[WorkerConfig]:
    return [
        WorkerConfig("worker-00", "emulator-5554", "avd-0", 8081, 18766, True),
        WorkerConfig("worker-01", "emulator-5556", "avd-1", 8082, 18767, True),
    ]


class DispatchDecisionTests(unittest.TestCase):
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
        self.manager = api.JobManager("emulator-5554", workers=workers())

    def plan(self, *, queue_depth: int = 1):
        with self.manager._lock:
            return self.manager._dispatch_plan_locked(queue_depth=queue_depth)

    def test_active_worker_is_selected_when_idle(self):
        plan = self.plan()
        self.assertEqual([device.device_id for device in plan.assignments], ["worker-00"])

    def test_standby_is_not_offered_to_the_live_loop(self):
        # Failover to a standby is the recovery path's job, not the dispatcher's.
        plan = self.plan()
        self.assertIn("worker-01", plan.blocked)
        self.assertNotIn("worker-01", [d.device_id for d in plan.assignments])

    def test_zero_queue_depth_yields_no_assignment(self):
        self.assertEqual(self.plan(queue_depth=0).assignments, ())

    def test_cooldown_blocks_the_device(self):
        self.manager._worker_runtime["worker-00"]["cooldown_until"] = 9_999_999_999.0
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "cooldown")

    def test_job_in_flight_blocks_the_device(self):
        self.manager._active_job_id = "fb_running"
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "busy")

    def test_failed_worker_is_not_offered(self):
        self.manager._failed_worker_ids.add("worker-00")
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "disabled")

    def test_orphaned_worker_is_not_offered(self):
        self.manager._orphaned_worker_ids.add("worker-00")
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "disabled")

    def test_disabled_worker_is_not_offered(self):
        self.manager.workers["worker-00"] = replace(self.manager.workers["worker-00"], enabled=False)
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "disabled")

    def test_unverified_capture_lease_blocks_the_device(self):
        self.manager._capture_lease_uncertain = True
        plan = self.plan()
        self.assertEqual(plan.assignments, ())
        self.assertEqual(plan.blocked["worker-00"], "lease-held")

    def test_target_helper_returns_the_worker_config(self):
        with self.manager._lock:
            target = self.manager._dispatch_target_locked()
        self.assertIsNotNone(target)
        self.assertEqual(target.worker_id, "worker-00")
        self.manager._capture_lease_uncertain = True
        with self.manager._lock:
            self.assertIsNone(self.manager._dispatch_target_locked())


if __name__ == "__main__":
    unittest.main()
