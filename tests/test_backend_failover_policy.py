"""Exercise the default production manager factory with file-backed SQLite and synthetic hardware."""
from __future__ import annotations

import csv
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import select

import facebook_capture_api as api
from facebook_backend.config import Settings
from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.main import _build_default_manager
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.job_store import DatabaseJobStore
from facebook_backend.persistence.lifecycle_events import replay_job_events
from facebook_backend.persistence.models import Base, JobEvent
from facebook_backend.persistence.repositories import DeviceRepository, JobRepository, LeaseRepository
from facebook_backend.scheduler import production_policy
from facebook_backend.scheduler.device_registry import default_worker
from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.production_native import NativeAttempt


ONLINE = {'state': 'device', 'boot_completed': True, 'proxy_error': 'TimeoutExpired'}
OFFLINE = {'state': 'offline', 'boot_completed': False}


class ProductionFailoverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.url = 'sqlite+pysqlite:///' + (self.root / 'store.sqlite').as_posix()
        self.settings = Settings(base_dir=self.root, database_url=self.url, load_local_config=False,
                                 job_store='database')
        self.enterContext(mock.patch.object(api, 'BASE_DIR', self.root))
        self.probe = self.enterContext(mock.patch.object(api, 'adb_snapshot', autospec=True, return_value=ONLINE))
        self.native = self.enterContext(mock.patch.object(NativeAttempt, 'execute', autospec=True))
        self.enterContext(mock.patch.object(api, 'run_capture_job', side_effect=AssertionError('real capture')))
        primary = default_worker('fixture-A')
        standby = replace(primary, worker_id='standby', serial='fixture-B', avd_name='avd-B', capture_port=9012,
                          native_port=9013)
        (self.root / 'facebook_workers.json').write_text(json.dumps({
            'version': 1, 'workers': [asdict(primary), asdict(standby)]}), encoding='utf-8')
        self.fixture = {'post_url': 'https://www.facebook.com/fixture/posts/1'}
        self.db = Database.open(self.url)
        self.addCleanup(self.db.dispose)
        Base.metadata.create_all(self.db.engine)

    def manager(self):
        # A fresh engine, Session factory, store and facade for every restart.
        db = Database.open(self.url)
        self.addCleanup(db.dispose)
        manager = _build_default_manager(self.settings, DatabaseJobStore(db))
        self.addCleanup(manager.shutdown, 3)
        return manager

    def row(self, job):
        with self.db.session() as session:
            return JobRepository(session).require(job)

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail('timed out waiting for transaction')

    @staticmethod
    def outcome(native, failure=None, *, process='stopped', cleanup='restored'):
        return ExecutionOutcome(native.claim.assignment.job_id, 'failed' if failure else 'completed', failure=failure,
                                result=None if failure else {'fixture': 'verified'}, process_state=process,
                                attempt_id=native.claim.attempt_id, transport_cleanup_state=cleanup)

    def test_takeover_preserves_partial_checkpoint_fifo_and_distinct_proven_attempts_across_restarts(self):
        self.probe.side_effect = lambda serial, **_: OFFLINE if serial == 'fixture-A' else ONLINE
        first_started, finish_first = threading.Event(), threading.Event()
        calls = []
        def capture(native, _adapter, _event):
            a = native.claim.assignment
            calls.append((a.job_id, a.attempt, a.device.serial))
            if len(calls) == 1:
                analysis = Path(a.run_dir) / 'analysis'
                analysis.mkdir(parents=True)
                with (analysis / 'facebook-reactor-users.csv').open('w', newline='', encoding='utf-8') as stream:
                    writer = csv.DictWriter(stream, fieldnames=api.REACTOR_USER_FIELDS)
                    writer.writeheader()
                    writer.writerow({'user_id': 'synthetic', 'source_post_id': 'p', 'reaction_id': 'LIKE'})
                first_started.set()
                if not finish_first.wait(5):
                    raise AssertionError('test did not release first attempt')
                return self.outcome(native, FailureClass.DEVICE)
            return self.outcome(native)
        self.native.side_effect = capture
        manager = self.manager()
        first = manager.submit(self.fixture)['job_id']
        self.assertTrue(first_started.wait(5))
        second = manager.submit(self.fixture)['job_id']
        self.assertFalse(manager.shutdown(0)['drained'])
        finish_first.set()
        self.assertTrue(manager.shutdown(4)['drained'])
        pending = self.row(first)
        self.assertEqual((pending['status'], pending['pinned_device_id'], pending['failover_count']), ('queued', 'standby', 1))
        self.assertEqual(pending['payload']['checkpoint']['preserved_user_count'], 1)
        self.assertIsNone(pending['result'])
        self.assertEqual(calls, [(first, 1, 'fixture-A')])
        self.assertEqual([c.args[0] for c in self.probe.call_args_list], ['fixture-A'] * 3 + ['fixture-B'])
        with self.db.session() as session:
            self.assertEqual(LeaseRepository(session).list_active(), [])
            self.assertEqual(JobRepository(session).attempts(first)[0]['result_status'], 'failed')
        # The standby was previously healthy, but a restart requires a new positive probe.
        self.probe.side_effect = lambda *_a, **_kw: OFFLINE
        unavailable = self.manager()
        self.assertIsNone(unavailable._active_worker_id)
        self.assertEqual(self.row(first)['attempt_count'], 1)
        self.assertTrue(unavailable.shutdown(2)['drained'])
        self.probe.side_effect = lambda *_a, **_kw: ONLINE
        restarted = self.manager()
        self.wait(lambda: self.row(second)['status'] == 'completed')
        self.assertEqual(calls, [(first, 1, 'fixture-A'), (first, 2, 'fixture-B'), (second, 1, 'fixture-B')])
        self.assertEqual(restarted._started_worker_ids, {'standby'})
        self.assertEqual([Path(d).name for d in self.row(first)['payload']['attempt_dirs']], ['attempt-001', 'attempt-002'])
        with self.db.session() as session:
            attempts = JobRepository(session).attempts(first)
            self.assertEqual([a['result_status'] for a in attempts], ['failed', 'completed'])
            events = [(e.event_type, e.attempt_id) for e in session.scalars(select(JobEvent).where(
                JobEvent.job_id == first).order_by(JobEvent.event_id))]
            previous, next_attempt = [a['attempt_id'] for a in attempts]
            names = ['job.process_stopped', 'job.cleanup_completed', 'job.failover']
            positions = [events.index((name, previous)) for name in names] + [events.index(('job.claimed', next_attempt))]
            self.assertEqual(positions, sorted(positions))
            self.assertTrue(replay_job_events(session, first).matches_projection)
            self.assertEqual(LeaseRepository(session).list_active(), [])

    def test_clean_executor_failure_and_unconfirmed_device_are_terminal(self):
        # A healthy boot with unknown proxy is a positive device diagnostic.
        self.probe.side_effect = [OFFLINE, ONLINE]
        self.native.side_effect = lambda native, *_: self.outcome(native,
            FailureClass.EXECUTOR if native.claim.assignment.request['post_url'].endswith('/1') else FailureClass.DEVICE)
        m = self.manager()
        ids = [m.submit(dict(post_url='https://www.facebook.com/fixture/posts/' + n))['job_id'] for n in ('1', '2')]
        self.wait(lambda: all(self.row(key)['status'] == 'failed' for key in ids))
        self.assertEqual([self.row(key)['attempt_count'] for key in ids], [1, 1])
        self.assertEqual(m._active_worker_id, 'worker-01')
        self.assertEqual(m._failed_worker_ids, set())
        self.assertEqual([c.args[0] for c in self.probe.call_args_list], ['fixture-A', 'fixture-A'])

    def test_device_failure_and_partial_cleanup_keep_global_lease_on_restart(self):
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.DEVICE, cleanup='pending')
        manager = self.manager()
        first = manager.submit(self.fixture)['job_id']
        self.wait(lambda: self.row(first)['recovery_state'] == 'execution-unverified')
        second = manager.submit(self.fixture)['job_id']
        self.assertFalse(manager.shutdown(2)['drained'])
        restarted = self.manager()
        self.assertTrue(restarted._capture_lease_uncertain)
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(self.row(first)['failover_count'], 0)
        self.probe.assert_not_called()
        self.assertEqual(self.native.call_count, 1)
        with self.db.session() as session:
            self.assertEqual([r['job_id'] for r in LeaseRepository(session).list_active()], [first])

    def test_network_retries_stay_pinned_with_persistent_deadlines_and_attempt_cap(self):
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.NETWORK)
        manager = self.manager()
        first = manager.submit(self.fixture)['job_id']
        clock = time.time()
        for count, delay in [(1, 30), (2, 120)]:
            self.wait(lambda: self.row(first)['attempt_count'] == count and self.row(first)['status'] == 'queued')
            self.assertTrue(manager.shutdown(2)['drained'])
            self.assertEqual(self.row(first)['pinned_device_id'], 'worker-01')
            self.assertEqual(self.row(first)['payload']['max_attempts'], 3)
            if count == 1:
                self.enterContext(mock.patch.object(production_policy, '_POLICY',
                    replace(production_policy._POLICY, max_attempts=5)))
            with self.db.session() as session:
                deadline = DeviceRepository(session).get('worker-01')['cooldown_until']
                self.assertGreaterEqual(deadline, clock + delay - 2)
            manager = self.manager()
            self.assertEqual(manager._worker_runtime['worker-01']['cooldown_until'], deadline)
            self.assertIsNone(manager._production.tx.claim(manager.workers['worker-01']))
            self.assertTrue(manager.shutdown(2)['drained'])
            # Jump the wall clock beyond the persisted deadline without rewriting DB state.
            clock = deadline + 1
            self.enterContext(mock.patch('time.time', return_value=clock))
            manager = self.manager()
        self.wait(lambda: self.row(first)['status'] == 'failed')
        self.assertEqual(self.row(first)['attempt_count'], 3)
        self.assertEqual(self.row(first)['failover_count'], 0)
        self.assertEqual([c.args[0].worker.serial for c in self.native.call_args_list], ['fixture-A'] * 3)
        self.probe.assert_not_called()

    def test_limit_never_switches_identity_even_with_healthy_standby(self):
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.RATE_LIMIT)
        manager = self.manager()
        first = manager.submit(self.fixture)['job_id']
        self.wait(lambda: self.row(first)['status'] == 'failed')
        second = manager.submit(self.fixture)['job_id']
        self.assertTrue(manager.shutdown(2)['drained'])
        restarted = self.manager()
        self.assertEqual(self.row(first)['pinned_device_id'], 'worker-01')
        self.assertEqual(restarted._active_worker_id, 'worker-01')
        self.assertGreater(restarted._worker_runtime['worker-01']['cooldown_until'], time.time())
        self.assertEqual(self.row(second)['status'], 'queued')
        self.assertEqual(self.native.call_count, 1)
        self.probe.assert_not_called()

    def test_only_one_takeover_for_a_job_even_with_a_third_healthy_worker(self):
        config = self.root / 'facebook_workers.json'
        registry = json.loads(config.read_text(encoding='utf-8'))
        registry['workers'].append(dict(registry['workers'][1], worker_id='third', serial='fixture-C',
                                        avd_name='avd-C', capture_port=9014, native_port=9015))
        config.write_text(json.dumps(registry), encoding='utf-8')
        # B is verified healthy for takeover, then also suffers a confirmed fault.
        self.probe.side_effect = [OFFLINE] * 3 + [ONLINE] + [OFFLINE] * 3 + [ONLINE]
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.DEVICE)
        m = self.manager()
        first = m.submit(self.fixture)['job_id']
        self.wait(lambda: self.row(first)['status'] == 'failed')
        self.assertEqual(m._active_worker_id, 'third')
        self.assertEqual(self.row(first)['failover_count'], 1)
        self.assertEqual(self.row(first)['attempt_count'], 2)
        self.assertEqual([c.args[0].worker.serial for c in self.native.call_args_list], ['fixture-A', 'fixture-B'])

    def test_confirmed_offline_without_positive_standby_probe_stays_parked(self):
        self.probe.return_value = OFFLINE
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.DEVICE)
        m = self.manager()
        first = m.submit(self.fixture)['job_id']
        self.wait(lambda: self.row(first)['status'] == 'failed')
        next_job = m.submit(self.fixture)['job_id']
        self.assertTrue(m.shutdown(2)['drained'])
        restarted = self.manager()
        self.assertIsNone(restarted._active_worker_id)
        self.assertEqual(self.row(next_job)['status'], 'queued')
        self.assertEqual(self.row(first)['failover_count'], 0)
        self.assertEqual(self.native.call_count, 1)

    def test_failure_to_commit_requeue_keeps_original_owner_and_prevents_promotion(self):
        self.probe.side_effect = lambda serial, **_: OFFLINE if serial == 'fixture-A' else ONLINE
        self.native.side_effect = lambda native, *_: self.outcome(native, FailureClass.DEVICE)
        self.enterContext(mock.patch.object(LeaseRepository, 'release', autospec=True, side_effect=OSError('disk')))
        manager = self.manager()
        first = manager.submit(self.fixture)['job_id']
        self.wait(lambda: self.row(first)['recovery_state'] == 'failed-result-persistence')
        self.assertFalse(manager.shutdown(2)['drained'])
        restarted = self.manager()
        self.assertTrue(restarted._capture_lease_uncertain)
        self.assertEqual(self.row(first)['failover_count'], 0)
        with self.db.session() as session:
            self.assertIsNone(DeviceRepository(session).get('standby'))
            self.assertEqual(LeaseRepository(session).list_active()[0]['job_id'], first)
            self.assertIsNone(JobRepository(session).attempts(first)[0]['result_status'])
        self.assertEqual(self.native.call_count, 1)
