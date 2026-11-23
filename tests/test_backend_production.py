"""Production path regressions. Every capture, process and device is synthetic."""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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
from facebook_backend.platform.owned_process import OwnedProcess
from facebook_backend.scheduler.production_native import NativeAttempt
from facebook_backend.scheduler.production_transactions import ProductionTransactions


def native_receipt_name():
    name = 'resume_windows_watchdog' if api.sys.platform == 'win32' else 'verify_transport_receipt'
    return 'facebook_backend.platform.transport_receipt.' + name


REAL_RUN_CAPTURE_JOB = api.run_capture_job


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
        proc = mock.create_autospec(OwnedProcess, instance=True)
        proc.pid, proc.poll.return_value, proc.returncode = 909999, 0, 0
        proc.active_processes.return_value = 0
        self.enterContext(mock.patch.object(OwnedProcess, 'start', autospec=True, return_value=proc))
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker,
                    transport_attempt_id=None, process_factory=None):
            self.assertEqual(worker.serial, serial)
            self.assertEqual(timeout_seconds, 42)
            self.assertEqual(run_dir, self.root / 'runs' / ('api-' + job_id) / 'attempt-001')
            capture_process_callback()
            self.assertEqual(self.row(job_id)['transport_cleanup_state'], 'pending')
            self.assertTrue(transport_attempt_id)
            process_callback(process_factory(["fixture"]) if process_factory else proc)
            self.assertEqual(self.row(job_id)['process_pid'], proc.pid)
            process_callback(None)
            return {'fixture': 'native attempt'}
        api.run_capture_job.side_effect = capture
        self.enterContext(mock.patch('facebook_backend.platform.process_manager.creation_time', autospec=True, return_value=9.0))
        merged = {'completion_scope': 'terminal', 'fixture': 'verified result'}
        self.enterContext(mock.patch.object(api, 'materialize_capture_attempts', autospec=True))
        self.enterContext(mock.patch.object(api, 'build_api_result', autospec=True, return_value=merged))
        self.enterContext(mock.patch('facebook_backend.platform.process_manager.presence', autospec=True, return_value=False))
        self.enterContext(mock.patch.object(api.JobManager, '_macos_runner_cleanup_result', autospec=True,
                        return_value={'transport_cleanup_state': 'runner-owned', 'transport_cleanup_error': None}))
        receipt = self.enterContext(mock.patch(native_receipt_name(),
                                              autospec=True, return_value=True))
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
            self.assertEqual(receipt.call_args.kwargs['runner_pid'], proc.pid)
            self.assertEqual(receipt.call_args.kwargs['job_id'], job_id)
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

    def test_the_real_production_path_emits_task_level_metrics(self):
        """The observability helpers must be wired to the live path, not just exist."""
        from facebook_backend.observability.metrics import METRICS
        METRICS.reset()
        executor = mock.create_autospec(ExecutorPort, instance=True)
        executor.execute.side_effect = lambda a, *, cancel_event: ExecutionOutcome(
            a.job_id, 'completed', result={'completion_scope': 'terminal', 'fixture': True},
            process_state='stopped', transport_cleanup_state='restored')
        m = self.manager(executor)
        job_id = m.submit(self.payload)['job_id']
        self.wait(lambda: self.row(job_id)['status'] == 'completed')
        self.wait(lambda: m._active_job_id is None)
        self.assertTrue(m.shutdown(2)['drained'])
        rendered = METRICS.render()
        # A committed terminal transition is counted once, with the real status.
        self.assertIn('facebook_jobs_completed_total{status="completed"} 1.0', rendered)
        # Dispatch and execution were actually timed, and the busy gauge is back to zero.
        self.assertIn('facebook_dispatch_seconds_count{decision="claimed"} 1', rendered)
        self.assertIn('facebook_job_execution_seconds_count{status="completed"} 1', rendered)
        self.assertIn('facebook_queue_wait_seconds_count 1', rendered)
        self.assertIn('facebook_process_stop_verifications_total{result="stopped"} 1.0', rendered)
        self.assertIn('facebook_worker_busy 0.0', rendered)
        self.assertNotIn('facebook_jobs_completed_total{status="failed"}', rendered)

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
        process = mock.create_autospec(OwnedProcess, instance=True)
        process.pid, process.poll.return_value = 909999, None
        self.enterContext(mock.patch.object(OwnedProcess, 'start', autospec=True, return_value=process))
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker,
                    transport_attempt_id=None, process_factory=None):
            capture_process_callback()
            started.set()
            self.assertTrue(late.wait(5))
            process_callback(process_factory(["fixture"]) if process_factory else process)
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
        if api.sys.platform == 'win32':
            process.terminate_root.assert_called_once_with()
            terminate.assert_not_called()
        else:
            terminate.assert_called_once_with(process)

    def test_callback_write_error_leaves_lease_and_returns_handle_to_wait(self):
        from facebook_backend.scheduler.production import ProductionScheduler
        from facebook_backend.platform import process_manager
        process = mock.create_autospec(subprocess.Popen, instance=True)
        process.pid, process.poll.return_value = 909999, 0
        callback_after_failed_receipt = threading.Event()
        def capture(request, job_id, serial, timeout_seconds, process_callback, capture_process_callback, run_dir, worker,
                    transport_attempt_id=None, process_factory=None):
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

    def test_native_launcher_preserves_arguments_and_timeout_only_stops_root(self):
        proc = mock.create_autospec(OwnedProcess, instance=True)
        proc.pid = 909999
        proc.wait.side_effect = subprocess.TimeoutExpired('fixture', 7)
        proc.terminate_root.return_value = True
        launcher = mock.create_autospec(OwnedProcess.start, return_value=proc)
        bootstrap = self.enterContext(mock.patch.object(api, 'ensure_emulator', autospec=True))
        self.enterContext(mock.patch.object(api, 'build_capture_command', autospec=True, return_value=['fixture']))
        tree = self.enterContext(mock.patch.object(api, '_terminate_tree', autospec=True))
        real_spawn = self.enterContext(mock.patch.object(api.subprocess, 'Popen', autospec=True))
        events = []
        callback = lambda process: events.append(process)
        directory = self.root / 'runs' / 'fixture' / 'attempt-001'
        old = dict(os.environ)
        with self.assertRaisesRegex(api.ApiError, 'timed out after 7s'):
            REAL_RUN_CAPTURE_JOB(self.payload, 'fb_fixture', self.worker.serial, timeout_seconds=7,
                process_callback=callback, capture_process_callback=lambda: events.append('pending'),
                run_dir=directory, worker=self.worker, process_factory=launcher, transport_attempt_id='att_fixture')
        bootstrap.assert_called_once_with(worker=self.worker, process_callback=callback, bootstrap_root_only=True)
        self.assertEqual(events, ['pending', proc, None])
        self.assertEqual(launcher.call_args.args, (['fixture'],))
        kwargs = launcher.call_args.kwargs
        self.assertEqual(kwargs['cwd'], str(self.root))
        self.assertEqual(kwargs['stdout'].name, str(directory / 'capture.log'))
        self.assertIs(kwargs['stderr'], subprocess.STDOUT)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['env']['FACEBOOK_TRANSPORT_ATTEMPT_ID'], 'att_fixture')
        self.assertEqual(kwargs['env']['FACEBOOK_TRANSPORT_JOB_ID'], 'fb_fixture')
        self.assertEqual(os.environ.get('FACEBOOK_TRANSPORT_ATTEMPT_ID'), old.get('FACEBOOK_TRANSPORT_ATTEMPT_ID'))
        proc.wait.assert_called_once_with(timeout=7)
        proc.terminate_root.assert_called_once_with()
        proc.terminate_owned.assert_not_called()
        tree.assert_not_called()
        real_spawn.assert_not_called()

    def test_live_or_unknown_descendants_and_missing_receipt_preserve_lease(self):
        self.store.save_job(dict(job_id='fb_fixture', request=self.payload, status='queued'))
        tx = ProductionTransactions(self.store, self.root)
        claim = tx.claim(self.worker)
        attempt = NativeAttempt(None, claim, self.worker)
        proc = mock.create_autospec(OwnedProcess, instance=True)
        proc.pid, proc.returncode, proc.poll.return_value = 909999, 0, 0
        attempt.owned_process = attempt.runner_handle = proc
        attempt.handles = [proc]
        attempt.result_verified = True
        attempt.started_at = time.time()
        self.enterContext(mock.patch('facebook_backend.scheduler.production_native.sys.platform', 'win32'))
        reader = self.enterContext(mock.patch(native_receipt_name(),
                                              autospec=True, return_value=False))
        for count in (1, None):
            proc.active_processes.return_value = count
            self.assertEqual(attempt.proof(api), (None, 'pending'))
        reader.assert_not_called()
        proc.active_processes.return_value = 0
        self.assertEqual(attempt.proof(api), ('stopped', 'pending'))
        self.assertEqual(self.holders()[0]['job_id'], 'fb_fixture')

    def test_bootstrap_failure_terminates_only_the_owned_root(self):
        process = mock.create_autospec(subprocess.Popen, instance=True)
        process.poll.side_effect = [None, 1]
        (self.root / 'start_facebook_stable.ps1').touch()
        self.enterContext(mock.patch.object(api.sys, 'platform', 'win32'))
        self.enterContext(mock.patch.object(api.shutil, 'which', return_value='fixture'))
        api.adb_snapshot.return_value = {'state': 'offline', 'boot_completed': False}
        self.enterContext(mock.patch.object(api.subprocess, 'Popen', autospec=True, return_value=process))
        tree = self.enterContext(mock.patch.object(api, '_terminate_tree', autospec=True))
        registered = []
        with self.assertRaisesRegex(api.WorkerUnavailableError, 'did not become ready'):
            api.ensure_emulator(worker=self.worker, timeout_seconds=0, bootstrap_root_only=True,
                                process_callback=registered.append)
        tree.assert_not_called()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        self.assertEqual(registered, [process, None])

    def test_committed_launch_is_one_shot_and_validates_attempt_record(self):
        self.store.save_job(dict(job_id='fb_fixture', request=self.payload, status='queued'))
        tx = ProductionTransactions(self.store, self.root)
        claim = tx.claim(self.worker)
        self.assertEqual((self.row('fb_fixture')['process_state'], self.row('fb_fixture')['transport_cleanup_state']),
                         ('not-started', 'not-required'))
        with self.assertRaises(LeaseHeldError):
            tx.started(replace(claim, attempt_id='other'))
        self.assertTrue(tx.started(claim))
        self.assertEqual(self.row('fb_fixture')['process_state'], 'unknown')
        with self.assertRaises(LeaseHeldError):
            tx.started(claim)
        self.assertEqual(len(self.holders()), 1)

    def test_materialization_tracks_only_current_atomic_replacements(self):
        root = self.root / 'output'
        attempt = root / 'attempt-001'
        (attempt / 'analysis').mkdir(parents=True)
        (root / 'analysis').mkdir()
        (root / 'safe-facebook-http-capture.jsonl').write_text('stale', encoding='utf8')
        # An existing optional artifact and the regular (non-atomic) copy of a
        # sample report are both ineligible for a new completion digest.
        (attempt / 'analysis' / 'facebook-sample-patterns.json').write_text('{}', encoding='utf8')
        published = set()
        api.materialize_capture_attempts(root, [attempt], published=published)
        self.assertEqual(published, set())
        (attempt / 'safe-facebook-http-capture.jsonl').write_text('{"fixture":true}\n', encoding='utf8')
        (attempt / 'analysis' / 'facebook-reactor-users.csv').write_text(
            'user_id,user_name,source_post_id,reaction_id\nfixture,Test,post,LIKE\n', encoding='utf8')
        api.materialize_capture_attempts(root, [attempt], published=published)
        self.assertEqual(published, {'safe-facebook-http-capture.jsonl', 'facebook-reactor-users.csv'})

    def test_simultaneous_claims_for_different_workers_share_one_slot(self):
        for job_id in ('fb_a', 'fb_b'):
            self.store.save_job(dict(job_id=job_id, status='queued', request=self.payload))
        gate = threading.Barrier(2)
        def compete(worker):
            tx = ProductionTransactions(self.store, self.root)
            gate.wait(timeout=3)
            try:
                return tx.claim(worker).assignment.job_id
            except LeaseHeldError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete, worker) for worker in (
                self.worker, replace(self.worker, worker_id='standby', serial='standby-serial'))]
            outcomes = [future.result(timeout=5) for future in futures]
        self.assertEqual(outcomes.count(None), 1)
        self.assertEqual(len(self.holders()), 1)
        with self.db.session() as session:
            repo = JobRepository(session)
            self.assertEqual((repo.count('running'), repo.count('queued')), (1, 1))
