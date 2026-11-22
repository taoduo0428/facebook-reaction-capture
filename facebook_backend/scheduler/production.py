"""One production loop over committed SQLite jobs, hosted by the API facade."""
from __future__ import annotations

import logging
import threading

from facebook_backend.domain.exceptions import ApiError
from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.executors.legacy import LegacyCaptureExecutor
from facebook_backend.observability.tracing import execution_context
from facebook_backend.persistence.repositories import DeviceRepository, JobRepository
from facebook_backend.scheduler.production_native import NativeAttempt
from facebook_backend.scheduler.production_transactions import ProductionTransactions
from facebook_backend.scheduler.queue import write_session


class ProductionScheduler:
    """The facade supplies its lock and wake event; the SQL table owns ordering."""

    def __init__(self, manager, root, executor=None):
        self.manager = manager
        self.tx = ProductionTransactions(manager.store, root)
        self.executor = executor
        self.adapter = LegacyCaptureExecutor(timeout_seconds=manager.max_runtime_seconds or None)
        self.current = None
        self.cancel_event = None
        self.native = None

    def put(self, _job_id):
        self.manager._wake_event.set()  # Notification only; carries no execution authority.

    def qsize(self):
        with self.tx.database.session() as session:
            return JobRepository(session).count('queued')

    def restore(self):
        from facebook_backend.scheduler.production_recovery import recover_startup
        recovery_blocker = recover_startup(self.tx)
        m = self.manager
        for job, job_id in m.store.load_job_manifests():
            m._jobs[job_id] = job
            if job.get('submission_key'):
                m._submission_keys[job['submission_key']] = job_id
        with self.tx.database.session() as session:
            blocker = recovery_blocker or self.tx.blocker(session)
            for device in DeviceRepository(session).list():
                if device['worker_id'] in m._worker_runtime:
                    m._worker_runtime[device['worker_id']]['cooldown_until'] = device['cooldown_until'] or 0
        if blocker:
            m._block_capture_lease_locked(*blocker)

    def cancel(self, job_id):
        m = self.manager
        with m._lock:
            job = self.tx.cancel(job_id)
            m._jobs[job_id] = job
            active = self.current is not None and self.current.assignment.job_id == job_id
            native = self.native if active else None
            if active:
                self.cancel_event.set()
            m._wake_event.set()
        # The worker may be publishing a late handle under its own lock. Never
        # retain the manager lock across native tree termination or callback IO.
        if native is not None:
            native.cancel()
        accepted = job['status'] in {'cancelled', 'cancelling'}
        return {'cancelled': accepted, 'reason': 'cancellation requested' if job['status'] == 'cancelling'
                else job['status'], 'job': m.public_job(job_id)}

    def reconcile(self, job_id, *, acknowledge=False):
        if acknowledge is not True:
            raise ApiError('explicit acknowledgment of reviewed failed output is required')
        m = self.manager
        with m._lock:
            if self.current is not None:
                raise ApiError('an execution is still active')
            job, blocker = self.tx.reconcile(job_id)
            m._jobs[job_id] = job
            m._persistence_blocked_jobs.pop(job_id, None)
            if blocker:
                m._block_capture_lease_locked(*blocker)
            else:
                m._capture_lease_uncertain = False
                m._capture_lease_job_id = m._capture_lease_reason = None
                if m._active_worker_id is None:
                    m._active_worker_id = next((w.worker_id for w in m.workers.values() if w.enabled), None)
                if m._active_worker_id and m._accepting_submissions:
                    m._start_worker(m._active_worker_id)
                m._wake_event.set()
            return {'reconciled': True, 'job': m.public_job(job_id)}

    def record_callback(self, claim, **fields):
        m = self.manager
        with m._lock:
            with write_session(self.tx.database) as session:
                row = self.tx.owned(session, claim)
                row = JobRepository(session).update(row['id'], expected_version=row['version'], **fields)
                job = m.store._job_from_row(row)
            m._jobs[job['job_id']] = job

    def run(self):
        m = self.manager
        while True:
            try:
                with m._lock:
                    if not m._accepting_submissions:
                        return
                    m._wake_event.clear()
                    worker = m._dispatch_target_locked()
                    claim = self.tx.claim(worker) if worker and not m._capture_lease_uncertain else None
                    if claim is not None:
                        self.current = claim
                        self.cancel_event = threading.Event()
                        self.native = NativeAttempt(self, claim, worker) if self.executor is None else None
                        m._jobs[claim.assignment.job_id] = claim.job
                        m._active_job_id = claim.assignment.job_id
                        m._worker_runtime[worker.worker_id]['current_job_id'] = m._active_job_id
                if claim is not None:
                    self._run_claim(claim)
                    continue
            except Exception as exc:
                # An uncommitted write or stale ownership must never trigger
                # failover. Keep the live API available for inspection/shutdown.
                logging.getLogger(__name__).error('production dispatch held: %s', type(exc).__name__)
                with m._lock:
                    m._block_capture_lease_locked(m._active_job_id, 'production dispatch transaction unverified')
            m._wake_event.wait(1.0)

    def _run_claim(self, claim):
        m = self.manager
        a = claim.assignment
        worker_id = a.device.worker_id
        with execution_context(claim.job.get('traceparent'), job_id=a.job_id, batch_id=claim.job.get('batch_id'),
                               worker_id=worker_id, device_serial=a.device.serial, attempt_id=claim.attempt_id,
                               request_id=claim.job.get('request_id')):
            outcome = None
            publications = set()
            try:
                with m._lock:
                    started = self.tx.started(claim)
                    if started:
                        m._jobs[a.job_id] = started
                if not started:
                    outcome = ExecutionOutcome(a.job_id, 'cancelled', process_state='not-started',
                                               transport_cleanup_state='not-required', attempt_id=claim.attempt_id)
                elif self.native is not None:
                    outcome = self.native.execute(self.adapter, self.cancel_event)
                else:
                    outcome = self.executor.execute(a, cancel_event=self.cancel_event)
                with m._lock:
                    job, released = self.tx.finish(claim, outcome, cooldown_seconds=m.workers[worker_id].cooldown_seconds)
                    m._jobs[a.job_id] = job
                    if self.native is not None and released and job['status'] == 'completed':
                        publications = self.native.published
                    if not released:
                        m._block_capture_lease_locked(a.job_id, job['error'])
            except Exception:
                with m._lock:
                    m._block_capture_lease_locked(a.job_id, 'production outcome or persistence unverified', persistence=True)
                    # If this write also fails, the original committed lease and
                    # running row already prohibit a later claim across restart.
                    m._jobs[a.job_id] = self.tx.hold(claim, 'attempt outcome persistence unverified', storage=True, outcome=outcome)
            finally:
                with m._lock:
                    self.current = self.native = self.cancel_event = None
                    m._worker_runtime[worker_id]['current_job_id'] = None
                    m._active_job_id = None
            if publications:
                from facebook_backend.scheduler.production_publications import register_publications
                register_publications(self.tx, claim, publications)
            if claim.job.get('batch_id'):
                m._refresh_batch_outputs(claim.job['batch_id'])
