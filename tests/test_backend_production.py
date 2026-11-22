"""Production path regressions. Every capture, process and device is synthetic."""
from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import facebook_capture_api as api
from facebook_backend.executors.base import ExecutionOutcome, ExecutorPort
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.job_store import DatabaseJobStore
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import JobRepository, LeaseRepository
from facebook_backend.scheduler.device_registry import default_worker
from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.leases import LeaseHeldError
from facebook_backend.scheduler.production_native import NativeAttempt
from facebook_backend.scheduler.production_transactions import ProductionTransactions


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(api, 'BASE_DIR', self.root))
        self.enterContext(mock.patch.object(api, 'adb_snapshot', autospec=True, return_value={}))
        self.enterContext(mock.patch.object(api, 'run_capture_job', autospec=True,
                                          side_effect=AssertionError('unexpected capture')))
        self.db = Database.open('sqlite+pysqlite:///' + (self.root / 'jobs.db').as_posix())
        self.addCleanup(self.db.dispose)
        Base.metadata.create_all(self.db.engine)
        self.store = DatabaseJobStore(self.db)
        self.worker = default_worker('fixture-device')
        self.payload = {'post_url': 'https://www.facebook.com/fixture/posts/1'}

    def manager(self, executor=None):
        manager = api.JobManager(workers=[self.worker], max_runtime_seconds=42, store=self.store,
                                 production_executor=executor)
        self.addCleanup(manager.shutdown, 3)
        return manager

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail('timed out waiting for committed job transition')

    def row(self, job_id):
        with self.db.session() as session:
            return JobRepository(session).require(job_id)

    def holders(self):
        with self.db.session() as session:
            return LeaseRepository(session).list_active()

    def test_http_lifecycle_uses_actual_native_signature_and_stores_result(self):
        from fastapi.testclient import TestClient
        from facebook_backend.config import Settings
        from facebook_backend.main import create_app
        from facebook_backend.persistence.lifecycle_events import replay_job_events
        proc = mock.create_autospec(subprocess.Popen, instance=True)
        proc.pid, proc.poll.return_value = 909999, 0
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker):
            self.assertEqual(worker.serial, serial)
            self.assertEqual(timeout_seconds, 42)
            self.assertEqual(run_dir, self.root / 'runs' / ('api-' + job_id) / 'attempt-001')
            capture_process_callback()
            self.assertEqual(self.row(job_id)['transport_cleanup_state'], 'pending')
            process_callback(proc)
            self.assertEqual(self.row(job_id)['process_pid'], proc.pid)
            process_callback(None)
            return {'fixture': 'native attempt'}
        api.run_capture_job.side_effect = capture
        self.enterContext(mock.patch('facebook_backend.platform.process_manager.creation_time', autospec=True, return_value=9.0))
        merged = {'completion_scope': 'terminal', 'fixture': 'verified result'}
        self.enterContext(mock.patch.object(api, 'materialize_capture_attempts', autospec=True))
        self.enterContext(mock.patch.object(api, 'build_api_result', autospec=True, return_value=merged))
        self.enterContext(mock.patch.object(NativeAttempt, 'proof', autospec=True, return_value=('stopped', 'restored')))
        old_loop = self.enterContext(mock.patch.object(api.JobManager, '_worker_loop', autospec=True))
        from facebook_backend import main
        # Use the real default manager factory; replace only already-migrated store opening.
        self.enterContext(mock.patch.object(main, '_open_state_store', autospec=True, return_value=(self.store, None)))
        settings = Settings(base_dir=self.root, load_local_config=False, job_store='database',
                            max_runtime_seconds=42, database_url=str(self.db.engine.url))
        with TestClient(create_app(settings=settings), base_url='http://127.0.0.1') as client:
            response = client.post('/v1/jobs', json=self.payload)
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()['job_id']
            self.wait(lambda: self.row(job_id)['status'] == 'completed')
            self.assertEqual(client.get(f'/v1/jobs/{job_id}/result').json()['fixture'], 'verified result')
            self.assertEqual(self.holders(), [])
            self.assertEqual(self.row(job_id)['result']['fixture'], 'verified result')
            self.assertEqual(client.app.state.manager._worker.name, 'facebook-production-scheduler')
            old_loop.assert_not_called()
            self.assertEqual(api.run_capture_job.call_count, 1)
            with self.db.session() as session:
                self.assertTrue(replay_job_events(session, job_id).matches_projection)
                self.assertEqual(JobRepository(session).attempts(job_id)[0]['result_status'], 'completed')

    def test_unverified_cleanup_blocks_second_job_and_restart(self):
        executor = mock.create_autospec(ExecutorPort, instance=True)
        executor.execute.side_effect = lambda a, *, cancel_event: ExecutionOutcome(a.job_id, 'failed',
            error='fixture error', process_state='stopped', transport_cleanup_state='failed')
        m = self.manager(executor)
        first = m.submit(self.payload)['job_id']
        self.wait(lambda: self.row(first)['recovery_state'] == 'execution-unverified')
        second = m.submit(self.payload)['job_id']
        self.assertEqual(self.holders()[0]['job_id'], first)
        self.assertFalse(m.shutdown(2)['drained'])
        # Re-open against the same SQL file. No manifest or in-memory evidence is reused.
        restarted = self.manager(executor)
        self.assertTrue(restarted._capture_lease_uncertain)
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(executor.execute.call_count, 1)

    def test_active_cancel_is_cooperative_and_stop_prevents_second_claim(self):
        entered, proceed = threading.Event(), threading.Event()
        executor = mock.create_autospec(ExecutorPort, instance=True)
        def run(a, *, cancel_event):
            entered.set()
            self.assertTrue(proceed.wait(5))
            self.assertTrue(cancel_event.is_set())
            return ExecutionOutcome(a.job_id, 'cancelled', error='fixture cancelled',
                                    process_state='stopped', transport_cleanup_state='restored')
        executor.execute.side_effect = run
        m = self.manager(executor)
        first = m.submit(self.payload)['job_id']
        self.assertTrue(entered.wait(5))
        second = m.submit(self.payload)['job_id']
        self.assertEqual(m.cancel(first)['job']['status'], 'cancelling')
        self.assertFalse(m.shutdown(0)['drained'])
        proceed.set()
        self.assertTrue(m.shutdown(3)['drained'])
        self.assertFalse(m._worker.is_alive())
        self.assertEqual(self.row(first)['status'], 'cancelled')
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(self.holders(), [])
        self.assertEqual(executor.execute.call_count, 1)

    def test_result_write_error_rolls_back_release_and_survives_restart(self):
        executor = mock.create_autospec(ExecutorPort, instance=True)
        executor.execute.side_effect = lambda a, *, cancel_event: ExecutionOutcome(a.job_id, 'completed',
            result={'fixture': 1}, process_state='stopped', transport_cleanup_state='restored')
        fault = mock.patch.object(JobRepository, 'finish_attempt', autospec=True, side_effect=OSError('write'))
        fault.start()
        self.addCleanup(fault.stop)
        m = self.manager(executor)
        first = m.submit(self.payload)['job_id']
        self.wait(lambda: self.row(first)['recovery_state'] == 'failed-result-persistence')
        self.assertIsNone(self.row(first)['result'])
        self.assertEqual(m.public_job(first)['status'], 'running')
        self.assertEqual(self.holders()[0]['job_id'], first)
        self.assertFalse(m.shutdown(2)['drained'])
        restarted = self.manager(executor)
        self.assertTrue(restarted._capture_lease_uncertain)
        with self.assertRaises(api.ApiError):
            restarted.reconcile_result_persistence(first, acknowledge=False)
        with self.assertRaises(OSError):
            restarted.reconcile_result_persistence(first, acknowledge=True)
        self.assertEqual(self.holders()[0]['job_id'], first)
        fault.stop()
        self.assertTrue(restarted.reconcile_result_persistence(first, acknowledge=True)['reconciled'])
        self.assertEqual(self.row(first)['status'], 'failed')
        self.assertIsNone(self.row(first)['result'])
        self.assertEqual(self.holders(), [])

    def test_stale_token_or_fence_never_changes_job(self):
        self.store.save_job(dict(job_id='fb_fixture', request=self.payload, status='queued'))
        tx = ProductionTransactions(self.store, self.root)
        claim = tx.claim(self.worker)
        result = ExecutionOutcome(claim.assignment.job_id, 'completed', result={'fixture': 1},
                                  process_state='stopped', transport_cleanup_state='restored')
        for field, value in [('lease_token', 'stale'), ('fencing_version', 987), ('attempt', 123)]:
            bad = replace(claim, assignment=replace(claim.assignment, **{field: value}))
            with self.assertRaises(LeaseHeldError):
                tx.finish(bad, result)
        self.assertEqual(self.row('fb_fixture')['status'], 'running')
        with self.assertRaises(LeaseHeldError):
            tx.claim(replace(self.worker, worker_id='standby'))
        self.assertEqual(len(self.holders()), 1)

    def test_manifest_shutdown_while_active_does_not_dequeue_another_job(self):
        entered, proceed = threading.Event(), threading.Event()
        def run(manager, job_id, request, run_dir, worker):
            entered.set()
            proceed.wait(5)
            return {'fixture': True}
        call = self.enterContext(mock.patch.object(api.JobManager, '_execute_capture', autospec=True, side_effect=run))
        self.enterContext(mock.patch.object(api, 'materialize_capture_attempts', autospec=True))
        self.enterContext(mock.patch.object(api, 'build_api_result', autospec=True, return_value={'fixture': True}))
        self.enterContext(mock.patch.object(api.JobManager, '_checkpoint_job_locked', autospec=True))
        m = api.JobManager(workers=[self.worker])
        self.addCleanup(m.shutdown, 3)
        m.submit(self.payload)
        self.assertTrue(entered.wait(5))
        second = m.submit(self.payload)['job_id']
        self.assertFalse(m.shutdown(0)['drained'])
        proceed.set()
        m.shutdown(3)
        self.assertFalse(m._worker.is_alive())
        self.assertEqual(m.public_job(second)['status'], 'queued')
        self.assertEqual(call.call_count, 1)

    def test_rate_limited_device_remains_pinned_and_cooled_on_restart(self):
        executor = mock.create_autospec(ExecutorPort, instance=True)
        executor.execute.side_effect = lambda a, *, cancel_event: ExecutionOutcome(a.job_id, 'failed',
            error='fixture limit', failure=FailureClass.RATE_LIMIT,
            process_state='stopped', transport_cleanup_state='restored')
        m = self.manager(executor)
        first = m.submit(self.payload)['job_id']
        self.wait(lambda: self.row(first)['status'] == 'failed')
        self.assertEqual(self.row(first)['pinned_device_id'], self.worker.worker_id)
        self.assertTrue(m.shutdown(2)['drained'])
        restarted = self.manager(executor)
        self.assertGreater(restarted._worker_runtime[self.worker.worker_id]['cooldown_until'], time.time())
        second = restarted.submit(self.payload)['job_id']
        self.assertIsNone(restarted._production.tx.claim(self.worker))
        self.assertTrue(restarted.shutdown(2)['drained'])
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(executor.execute.call_count, 1)

    def test_native_late_registration_observes_durable_cancel(self):
        from facebook_backend.platform import process_manager
        started, late = threading.Event(), threading.Event()
        process = mock.create_autospec(subprocess.Popen, instance=True)
        process.pid, process.poll.return_value = 909999, None
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker):
            capture_process_callback()
            started.set()
            self.assertTrue(late.wait(5))
            process_callback(process)
            process_callback(None)
            raise RuntimeError('fixture cancelled')
        api.run_capture_job.side_effect = capture
        self.enterContext(mock.patch.object(process_manager, 'creation_time', autospec=True, return_value=9.0))
        terminate = self.enterContext(mock.patch.object(process_manager, 'terminate_tree', autospec=True, return_value=False))
        m = self.manager()
        first = m.submit(self.payload)['job_id']
        self.assertTrue(started.wait(5))
        m.cancel(first)
        late.set()
        self.wait(lambda: self.row(first)['recovery_state'] == 'execution-unverified')
        self.assertEqual(self.row(first)['status'], 'cancelling')
        self.assertEqual(self.row(first)['transport_cleanup_state'], 'pending')
        self.assertEqual(self.holders()[0]['job_id'], first)
        terminate.assert_called_once_with(process)

    def test_callback_write_error_leaves_lease_and_returns_handle_to_wait(self):
        from facebook_backend.scheduler.production import ProductionScheduler
        from facebook_backend.platform import process_manager
        process = mock.create_autospec(subprocess.Popen, instance=True)
        process.pid, process.poll.return_value = 909999, 0
        callback_after_failed_receipt = threading.Event()
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker):
            capture_process_callback()
            process_callback(process)
            callback_after_failed_receipt.set()
            process_callback(None)
            raise RuntimeError('fixture timeout')
        self.enterContext(mock.patch.object(process_manager, 'creation_time', autospec=True, return_value=9.0))
        self.enterContext(mock.patch.object(ProductionScheduler, 'record_callback', autospec=True, side_effect=OSError('db')))
        api.run_capture_job.side_effect = capture
        m = self.manager()
        first = m.submit(self.payload)['job_id']
        self.assertTrue(callback_after_failed_receipt.wait(5))
        self.wait(lambda: self.row(first)['recovery_state'] == 'execution-unverified')
        with self.assertRaises(api.ApiError):
            m.reconcile_result_persistence(first, acknowledge=True)
        self.assertEqual(self.holders()[0]['job_id'], first)
