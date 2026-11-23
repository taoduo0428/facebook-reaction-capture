"""Recovery at the actual app lifespan, using only temporary SQLite and fake captures."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import event as sql_events, select

import facebook_capture_api as api
from facebook_backend.config import Settings
from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.main import create_app
from facebook_backend.persistence.lifecycle_events import replay_job_events
from facebook_backend.persistence.models import Device, DeviceLease, JobAttempt, JobEvent, utc_now
from facebook_backend.persistence.repositories import BatchRepository, JobRepository, LeaseRepository
from facebook_backend.persistence.state_startup import open_state_store, state_owner
from facebook_backend.scheduler.device_registry import default_worker
from facebook_backend.scheduler.leases import LeaseHeldError
from facebook_backend.platform import process_manager
from facebook_backend.scheduler.production_native import NativeAttempt
from facebook_backend.scheduler.production_recovery import recover_startup
from facebook_backend.scheduler.production_transactions import ProductionTransactions


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.worker = default_worker('fixture-device')
        self.settings = Settings(base_dir=self.root, serial=self.worker.serial, load_local_config=False,
                                 shutdown_wait_seconds=1, log_level='WARNING')
        self.enterContext(mock.patch.object(api, 'BASE_DIR', self.root))
        self.enterContext(mock.patch.object(api, 'adb_snapshot', autospec=True, return_value={}))
        self.enterContext(mock.patch.object(api, 'run_capture_job', autospec=True,
                                           side_effect=AssertionError('unexpected native capture')))
        with state_owner(self.root):
            self.store, self.db = open_state_store(self.settings)
        self.addCleanup(self.db.dispose)
        self.tx = ProductionTransactions(self.store, self.root)
        self.request = {'post_url': 'https://www.facebook.com/fixture/posts/1'}
        self.job_id = 'fb_first'

    def seed(self):
        self.store.accept_job(dict(job_id=self.job_id, created_at=utc_now(), status='queued',
                                   pinned_device_id=self.worker.worker_id, submission_key='fixture-replay',
                                   request=self.request))
        return self.tx.claim(self.worker)

    def row(self):
        with self.db.session() as s:
            return JobRepository(s).require(self.job_id)

    def app_client(self):
        return TestClient(create_app(settings=self.settings), base_url='http://127.0.0.1')

    def test_default_factory_recovers_before_single_dispatch_and_preserves_idempotency(self):
        original = self.seed()
        starts = []
        actual_start = api.JobManager._start_worker
        entered = threading.Event()
        release = threading.Event()

        def start(manager, worker_id):
            # The whole recovery must already be committed from another connection.
            with self.db.session() as s:
                self.assertEqual(JobRepository(s).require(self.job_id)['status'], 'queued')
                self.assertEqual(LeaseRepository(s).list_active(), [])
                self.assertIsNone(s.get(Device, worker_id).current_job_id)
                self.assertTrue(s.get(JobAttempt, original.attempt_id).finished_at)
                event = s.scalar(select(JobEvent).where(JobEvent.event_type == 'job.recovered'))
                self.assertEqual((event.attempt_id, event.worker_id, event.device_serial),
                                 (original.attempt_id, worker_id, self.worker.serial))
                self.assertEqual(json.loads(event.payload)['fencing_version'], original.assignment.fencing_version)
                self.assertTrue(replay_job_events(s, self.job_id).matches_projection)
            starts.append(worker_id)
            return actual_start(manager, worker_id)

        def execute(native, adapter, cancel_event):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test did not release fake capture')
            return ExecutionOutcome(self.job_id, 'completed', result={'fixture': True}, process_state='stopped',
                                    transport_cleanup_state='restored', attempt_id=native.claim.attempt_id)

        self.enterContext(mock.patch.object(api.JobManager, '_start_worker', autospec=True, side_effect=start))
        fake = self.enterContext(mock.patch.object(NativeAttempt, 'execute', autospec=True, side_effect=execute))
        with self.app_client() as client:
            try:
                self.assertTrue(entered.wait(2))
                response = client.post('/v1/jobs', json=self.request, headers={'Idempotency-Key': 'fixture-replay'})
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(response.json()['job_id'], self.job_id)
                self.assertEqual(client.app.state.manager._worker.name, 'facebook-production-scheduler')
                self.assertEqual(self.row()['pinned_device_id'], self.worker.worker_id)
                self.assertEqual(self.row()['attempt_count'], 2)
            finally:
                release.set()
        from facebook_backend.observability.metrics import METRICS
        rendered = METRICS.render()
        # The provable requeue was published once, and the verification was timed.
        self.assertIn('facebook_recoveries_total{action="requeue"} 1.0', rendered)
        self.assertIn('facebook_recovery_duration_seconds_count', rendered)
        self.assertEqual(starts, [self.worker.worker_id])
        fake.assert_called_once()
        self.assertEqual(self.row()['status'], 'completed')

    def test_started_receipt_is_invalidated_before_executor_and_cannot_be_reused(self):
        claim = self.seed()
        self.assertTrue(self.tx.started(claim))
        with self.assertRaises(LeaseHeldError):
            self.tx.started(claim)
        # Crash here, after the permission to spawn has committed and before a PID callback.
        with self.app_client() as client:
            manager = client.app.state.manager
            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIsNone(manager._worker)
            with self.db.session() as s:
                self.assertEqual(LeaseRepository(s).list_active()[0]['lease_token'], claim.assignment.lease_token)
                self.assertIsNone(s.get(JobAttempt, claim.attempt_id).finished_at)
                self.assertEqual(replay_job_events(s, self.job_id).last_event_type, 'job.started')

    def assert_held(self, claim, *, status):
        with self.db.session() as s:
            self.assertEqual(self.row()['status'], status)
            self.assertEqual(s.get(Device, self.worker.worker_id).current_job_id, self.job_id)
            self.assertIsNone(s.get(JobAttempt, claim.attempt_id).finished_at)
            self.assertEqual(len(LeaseRepository(s).list_active()), 1)
            self.assertIsNone(s.scalar(select(JobEvent).where(JobEvent.event_type == 'job.recovered')))

    def test_cancel_before_spawn_is_terminal_and_repeat_startup_is_idempotent(self):
        claim = self.seed()
        self.tx.cancel(self.job_id)
        self.assertFalse(self.tx.started(claim))
        fake = self.enterContext(mock.patch.object(NativeAttempt, 'execute', autospec=True))
        for _ in range(2):
            with self.app_client() as client:
                self.assertEqual(client.app.state.manager.public_job(self.job_id)['status'], 'cancelled')
        fake.assert_not_called()
        with self.db.session() as s:
            events = list(s.scalars(select(JobEvent).where(JobEvent.event_type == 'job.recovered')))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].attempt_id, claim.attempt_id)
            self.assertEqual(s.get(JobAttempt, claim.attempt_id).result_status, 'cancelled')
            self.assertEqual(LeaseRepository(s).list_active(), [])
            self.assertTrue(replay_job_events(s, self.job_id).matches_projection)

    def test_running_after_handoff_with_result_dict_cleanup_and_expired_lease_stays_held(self):
        claim = self.seed()
        self.tx.started(claim)
        with self.db.session() as s:
            JobRepository(s).update(self.job_id, result={'ok': True}, process_state='stopped',
                                    transport_cleanup_state='restored')
            s.scalar(select(DeviceLease)).expires_at = '2000-01-01T00:00:00Z'
        for result in ({'ok': True}, None):
            with self.subTest(result=result):
                with self.db.session() as s:
                    JobRepository(s).update(self.job_id, result=result)
                self.assertEqual(recover_startup(self.tx)[0], self.job_id)
                self.assert_held(claim, status='running')

    def test_existing_attempt_directory_is_a_dispatch_contradiction(self):
        claim = self.seed()
        Path(claim.assignment.run_dir).mkdir(parents=True)
        with self.app_client() as client:
            manager = client.app.state.manager
            self.assertIsNone(manager._worker)
            self.assertIn('directory', manager._capture_lease_reason)
        self.assert_held(claim, status='running')

    def test_claim_projection_with_old_pid_or_missing_cleanup_never_requeues(self):
        claim = self.seed()
        with mock.patch.object(process_manager, 'identity_state', autospec=True, return_value='absent'):
            for pid, state in [(9021, 'not-required'), (None, 'pending'), (None, None)]:
                with self.subTest(pid=pid, state=state):
                    with self.db.session() as s:
                        JobRepository(s).update(self.job_id, process_pid=pid, transport_cleanup_state=state)
                    self.assertEqual(recover_startup(self.tx)[0], self.job_id)
                    self.assert_held(claim, status='running')

    def test_cancel_after_start_requires_both_attempt_receipts_and_no_uncertain_old_pid(self):
        claim = self.seed()
        self.tx.started(claim)
        self.tx.cancel(self.job_id)
        proof = dict(process_state='stopped', transport_cleanup_state='restored')
        with self.db.session() as s:
            row = JobRepository(s).update(self.job_id, process_pid=9021, process_creation_time=1.0, **proof)
        with mock.patch.object(process_manager, 'identity_state', autospec=True, return_value='absent') as identity:
            for event_type in ('job.process_stopped', 'job.cleanup_completed'):
                # Projection + absent alone, and then a single stop receipt, cannot release it.
                self.assertEqual(recover_startup(self.tx)[0], self.job_id)
                self.assert_held(claim, status='cancelling')
                with self.db.session() as s:
                    self.tx._event(s, row, event_type, 'cancelling', 'cancelling',
                                   attempt_id=claim.attempt_id, details=proof)
            for state in ('match', 'mismatch', 'unknown'):
                with self.subTest(state=state):
                    identity.return_value = state
                    self.assertEqual(recover_startup(self.tx)[0], self.job_id)
                    self.assert_held(claim, status='cancelling')
            identity.return_value = 'absent'
            self.assertIsNone(recover_startup(self.tx))
        self.assertEqual(self.row()['status'], 'cancelled')
        self.assertIsNone(self.row()['result'])

    def test_competing_and_later_finished_attempts_keep_original_lease(self):
        claim = self.seed()
        with self.db.session() as s:
            extra = JobRepository(s).record_attempt(job_id=self.job_id, attempt_index=2,
                                                    worker_id=self.worker.worker_id)
        self.assertEqual(recover_startup(self.tx)[0], self.job_id)
        self.assert_held(claim, status='running')
        with self.db.session() as s:
            JobRepository(s).finish_attempt(extra['attempt_id'], exit_code=None, result_status='failed')
        self.assertEqual(recover_startup(self.tx)[0], self.job_id)
        self.assert_held(claim, status='running')

    def test_claim_receipt_cannot_release_replaced_lease_for_same_job_and_worker(self):
        claim = self.seed()
        with self.db.session() as s:
            leases = LeaseRepository(s)
            leases.release(claim.assignment.lease_token, reason='fixture-replacement')
            replacement = leases.acquire(device_id=self.worker.worker_id, worker_id=self.worker.worker_id,
                                         job_id=self.job_id)
        self.assertNotEqual(claim.assignment.fencing_version, replacement['fencing_version'])
        self.assertEqual(recover_startup(self.tx)[0], self.job_id)
        with self.db.session() as s:
            self.assertEqual(LeaseRepository(s).list_active()[0]['lease_token'], replacement['lease_token'])
        self.assert_held(claim, status='running')

    def test_legacy_claim_without_typed_pre_spawn_proof_stays_held(self):
        append = self.tx._event

        def older_writer(session, job, name, before, after, **kwargs):
            if name == 'job.claimed':
                kwargs['details'].pop('process_state')
                kwargs['details'].pop('transport_cleanup_state')
            return append(session, job, name, before, after, **kwargs)

        with mock.patch.object(self.tx, '_event', side_effect=older_writer):
            claim = self.seed()
        self.assertEqual(recover_startup(self.tx)[0], self.job_id)
        self.assert_held(claim, status='running')

    def test_restart_racing_start_waits_for_commit_then_reads_consumed_proof(self):
        claim = self.seed()
        started_write = threading.Event()
        restart_reserving = threading.Event()
        release_writer = threading.Event()
        append = self.tx._event

        def pause_started(*args, **kwargs):
            result = append(*args, **kwargs)
            if args[2] == 'job.started':
                started_write.set()
                if not release_writer.wait(5):
                    raise TimeoutError('test writer timed out')
            return result

        def on_sql(conn, cursor, statement, parameters, context, many):
            if started_write.is_set() and statement == 'BEGIN IMMEDIATE':
                restart_reserving.set()

        sql_events.listen(self.db.engine, 'before_cursor_execute', on_sql)
        self.addCleanup(sql_events.remove, self.db.engine, 'before_cursor_execute', on_sql)
        with mock.patch.object(self.tx, '_event', side_effect=pause_started), ThreadPoolExecutor(2) as pool:
            started = pool.submit(self.tx.started, claim)
            try:
                self.assertTrue(started_write.wait(5))
                restart = pool.submit(recover_startup, self.tx)
                self.assertTrue(restart_reserving.wait(5))
            finally:
                release_writer.set()
            self.assertTrue(started.result(5))
            self.assertEqual(restart.result(5)[0], self.job_id)
        self.assert_held(claim, status='running')

    def test_result_write_hold_survives_even_cancellation_with_settled_resources(self):
        claim = self.seed()
        self.tx.started(claim)
        self.tx.cancel(self.job_id)
        outcome = ExecutionOutcome(self.job_id, 'cancelled', process_state='stopped',
                                   transport_cleanup_state='restored', attempt_id=claim.attempt_id)
        self.tx.hold(claim, 'fixture storage failure', storage=True, outcome=outcome)
        with self.app_client() as client:
            m = client.app.state.manager
            self.assertIsNone(m._worker)
            self.assertIn('result persistence', m._capture_lease_reason)
        self.assertEqual(self.row()['recovery_state'], 'failed-result-persistence')
        self.assertEqual(self.row()['error_message'], 'fixture storage failure')
        self.assert_held(claim, status='cancelling')

    def test_failed_recovery_rolls_back_attempt_job_batch_device_lease_and_event_together(self):
        batch_id = 'batch_fixture'
        job_ids = [self.job_id, 'fb_second']
        self.store.create_batch(dict(batch_id=batch_id, job_ids=job_ids, created_at=utc_now(),
                                     run_dir=str(self.root / batch_id)), [
            dict(job_id=job_id, status='queued', batch_id=batch_id, batch_index=i, request=self.request)
            for i, job_id in enumerate(job_ids, 1)])
        claim = self.tx.claim(self.worker)
        self.assertEqual(claim.assignment.job_id, job_ids[0])
        version = self.row()['version']
        release = LeaseRepository.release

        def failed_after_release(repo, *args, **kwargs):
            release(repo, *args, **kwargs)
            raise OSError('fixture commit failure')

        with mock.patch.object(LeaseRepository, 'release', autospec=True, side_effect=failed_after_release):
            with self.assertRaisesRegex(OSError, 'fixture commit failure'):
                recover_startup(self.tx)
        self.assertEqual(self.row()['version'], version)
        self.assert_held(claim, status='running')
        with self.db.session() as s:
            self.assertEqual(BatchRepository(s).get(batch_id)['status'], 'running')
        self.assertIsNone(recover_startup(self.tx))
        with self.db.session() as s:
            self.assertEqual(BatchRepository(s).job_ids(batch_id), job_ids)
            self.assertEqual(BatchRepository(s).get(batch_id)['status'], 'queued')
            self.assertEqual(s.get(JobAttempt, claim.attempt_id).result_status, 'requeued')
            self.assertTrue(replay_job_events(s, self.job_id).matches_projection)
        with self.assertRaises(LeaseHeldError):
            self.tx.started(claim)
        new = self.tx.claim(self.worker)
        self.assertEqual((new.assignment.job_id, new.assignment.attempt), (job_ids[0], 2))
        self.assertGreater(new.assignment.fencing_version, claim.assignment.fencing_version)
