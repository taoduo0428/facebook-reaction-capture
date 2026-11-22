"""SQLite transactions for the single production execution slot.

A claim makes the attempt, global reservation and running job visible together.
Neither startup nor clock expiry concludes an abandoned attempt was stopped.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from sqlalchemy import and_, func, or_, select

from facebook_backend.observability.context import snapshot
from facebook_backend.observability.tracing import trace_id_from_traceparent
from facebook_backend.persistence.lifecycle_events import append_lifecycle_event
from facebook_backend.persistence.models import Device as DeviceRow, Job, JobAttempt, utc_now
from facebook_backend.persistence.repositories import BatchRepository, DeviceRepository, JobRepository, LeaseRepository
from facebook_backend.domain.exceptions import ApiError
from facebook_backend.scheduler.production_policy import settle_failure
from facebook_backend.scheduler.leases import LeaseHeldError, safe_to_reuse
from facebook_backend.scheduler.models import Device, WorkerAssignment
from facebook_backend.scheduler.queue import write_session


@dataclass(frozen=True)
class Claim:
    assignment: WorkerAssignment
    attempt_id: str
    job: dict[str, Any]


class ProductionTransactions:
    def __init__(self, store, root: Path):
        self.store = store
        self.database = store.database
        self.root = Path(root)
        if not self.database.is_sqlite:
            raise ValueError('the production queue requires SQLite')

    def _event(self, session, row, name, before, after, *, attempt_id=None, details=None):
        append_lifecycle_event(
            session, job_id=row['id'], event_key=f"{name}:{row['version']}", event_type=name,
            before=before, after=after, batch_id=row['batch_id'], attempt_id=attempt_id,
            worker_id=row['assigned_worker_id'], device_serial=row['device_serial'],
            trace_id=snapshot().get('trace_id') or trace_id_from_traceparent(row['payload'].get('traceparent')),
            payload=details,
        )

    def blocker(self, session):
        leases = LeaseRepository(session).list_active()
        if leases:
            return leases[0]['job_id'], 'unreleased production lease'
        # Older writers could leave an active job without a lease or a terminal
        # header with an outstanding stop, transport or result-persistence hold.
        process = func.coalesce(Job.process_state, '')
        cleanup = func.coalesce(Job.transport_cleanup_state, '')
        settled = or_(and_(process == 'stopped', cleanup == 'restored'),
                      and_(process == 'not-started', cleanup == 'not-required'))
        blocked_id = session.scalar(select(Job.id).where(or_(
            Job.status.in_(['running', 'cancelling']), Job.recovery_state == 'failed-result-persistence',
            ~process.in_(['', 'stopped', 'not-started']),
            ~cleanup.in_(['', 'restored', 'not-required', 'runner-owned']),
            and_(Job.attempt_count > 0, ~settled),
            and_(Job.process_pid.is_not(None), ~settled),
        )).limit(1))
        return (blocked_id, 'prior attempt requires offline verification') if blocked_id else None

    def claim(self, worker):
        with write_session(self.database) as session:
            blocker = self.blocker(session)
            if blocker:
                raise LeaseHeldError(f'{blocker[0]}: {blocker[1]}')
            device_row = session.get(DeviceRow, worker.worker_id)
            if device_row and (device_row.health_status == 'policy-failed' or device_row.serial != worker.serial
                               or (device_row.cooldown_until or 0) > time.time()):
                return None
            # FIFO across submissions, preserving child positions inside a batch.
            candidate = session.scalar(select(Job).where(Job.status == 'queued').where(
                (Job.pinned_device_id.is_(None)) | (Job.pinned_device_id == worker.worker_id)
            ).order_by(Job.created_at, Job.batch_id, Job.batch_index, Job.id).limit(1))
            if candidate is None:
                return None
            repo = JobRepository(session)
            row = repo.to_dict(candidate)
            attempt = row['attempt_count'] + 1
            root = Path(row['run_dir'] or self.root / 'runs' / f"api-{row['id']}")
            root = root if root.is_absolute() else self.root / root
            directory = root / f'attempt-{attempt:03d}'
            payload = dict(row['payload'])
            payload['attempt_dirs'] = [*(payload.get('attempt_dirs') or []), str(directory)]
            payload.pop('error', None)
            row = repo.update(row['id'], expected_version=row['version'], status='running',
                              assigned_worker_id=worker.worker_id, device_serial=worker.serial,
                              attempt_count=attempt, started_at=utc_now(), finished_at=None,
                              recovery_state='claimed', process_state='not-started', process_pid=None,
                              process_creation_time=None, transport_cleanup_state='not-required',
                              error_message=None, payload=payload, result=None,
                              run_dir=str(root), capture_run_dir=str(directory))
            devices = DeviceRepository(session)
            devices.upsert(device_id=worker.worker_id, worker_id=worker.worker_id, serial=worker.serial,
                           avd_name=worker.avd_name, capture_port=worker.capture_port, native_port=worker.native_port,
                           enabled=worker.enabled, current_job_id=row['id'])
            lease = LeaseRepository(session).acquire(device_id=worker.worker_id, worker_id=worker.worker_id,
                                                    job_id=row['id'], ttl_seconds=900)
            record = repo.record_attempt(job_id=row['id'], attempt_index=attempt, worker_id=worker.worker_id,
                                         device_serial=worker.serial, run_dir=str(directory))
            self._event(session, row, 'job.claimed', 'queued', 'running', attempt_id=record['attempt_id'],
                        details={'attempt': attempt, 'fencing_version': lease['fencing_version'],
                                 'process_state': 'not-started', 'transport_cleanup_state': 'not-required'})
            if row['batch_id']:
                BatchRepository(session).recompute_counts(row['batch_id'])
            assignment = WorkerAssignment(row['id'], Device.from_worker_config(worker), dict(payload['request']),
                                          str(directory), attempt, lease['lease_token'], lease['fencing_version'])
            return Claim(assignment, record['attempt_id'], self.store._job_from_row(row))

    @staticmethod
    def owned(session, claim):
        a = claim.assignment
        row = JobRepository(session).require(a.job_id)
        lease = LeaseRepository(session).active(a.device.device_id)
        attempt = session.get(JobAttempt, claim.attempt_id)
        if (attempt is None or attempt.job_id != a.job_id or attempt.worker_id != a.device.worker_id
                or attempt.attempt_index != a.attempt or attempt.device_serial != a.device.serial or attempt.finished_at
                or not lease or lease['job_id'] != a.job_id or lease['worker_id'] != a.device.worker_id
                or lease['fencing_version'] != a.fencing_version or lease['lease_token'] != a.lease_token
                or row['attempt_count'] != a.attempt or row['assigned_worker_id'] != a.device.worker_id
                or row['status'] not in {'running', 'cancelling'}):
            raise LeaseHeldError('assignment no longer owns this job and device')
        return row

    def started(self, claim):
        with write_session(self.database) as session:
            row = self.owned(session, claim)
            if row['status'] == 'cancelling':
                return False
            if (row['recovery_state'], row['process_state'], row['transport_cleanup_state']) != (
                    'claimed', 'not-started', 'not-required'):
                raise LeaseHeldError('attempt was already started or has inconsistent launch evidence')
            row = JobRepository(session).update(row['id'], expected_version=row['version'], recovery_state='started',
                                                process_state='unknown', transport_cleanup_state='unknown')
            self._event(session, row, 'job.started', 'running', 'running', attempt_id=claim.attempt_id)
            return self.store._job_from_row(row)

    def cancel(self, job_id):
        with write_session(self.database) as session:
            repo = JobRepository(session)
            row = repo.get(job_id)
            if row is None:
                raise KeyError(job_id)
            before = row['status']
            if before in {'queued', 'running'}:
                status = 'cancelled' if before == 'queued' else 'cancelling'
                row = repo.update(job_id, expected_version=row['version'], status=status,
                                  cancel_requested_at=utc_now(), finished_at=utc_now() if before == 'queued' else None,
                                  recovery_state='cancellation-requested')
                self._event(session, row, 'job.cancellation_requested', before, status)
                if row['batch_id']:
                    BatchRepository(session).recompute_counts(row['batch_id'])
            return self.store._job_from_row(row)

    def hold(self, claim, reason, *, storage=False, outcome=None):
        with write_session(self.database) as session:
            row = self.owned(session, claim)
            payload = dict(row['payload'], error=reason)
            fields = {}
            if storage:
                payload['persistence_hold'] = {'attempt_id': claim.attempt_id, 'fence': claim.assignment.fencing_version,
                                               'token': claim.assignment.lease_token}
            if outcome is not None and outcome.job_id == row['id'] and outcome.attempt_id in {None, claim.attempt_id}:
                fields.update(process_state=outcome.process_state, transport_cleanup_state=outcome.transport_cleanup_state)
                if outcome.error:
                    payload['error'] = f'{reason}: {outcome.error}'
            row = JobRepository(session).update(row['id'], expected_version=row['version'], payload=payload,
                error_message=payload['error'], recovery_state='failed-result-persistence' if storage else 'execution-unverified',
                **fields)
            self._event(session, row, 'job.result_write_failed' if storage else 'job.worker_failed',
                        row['status'], row['status'], attempt_id=claim.attempt_id)
            return self.store._job_from_row(row)

    def finish(self, claim, outcome, *, cooldown_seconds=900, observation=None):
        if outcome.job_id != claim.assignment.job_id or (outcome.attempt_id and outcome.attempt_id != claim.attempt_id):
            raise LeaseHeldError('outcome belongs to another assignment')
        if not safe_to_reuse(outcome.process_state, outcome.transport_cleanup_state):
            return self.hold(claim, 'process stop or transport restoration is unverified', outcome=outcome), False
        if outcome.status == 'completed' and (outcome.process_state != 'stopped' or not outcome.result):
            return self.hold(claim, 'verified completion result is missing'), False
        with write_session(self.database) as session:
            row = self.owned(session, claim)
            before = row['status']
            evidence = {'process_state': outcome.process_state, 'transport_cleanup_state': outcome.transport_cleanup_state}
            for event in ('job.process_stopped', 'job.cleanup_completed'):
                self._event(session, row, event, before, before, attempt_id=claim.attempt_id, details=evidence)
            if before == 'running' and outcome.status == 'failed':
                return settle_failure(self, session, row, claim, outcome, cooldown_seconds=cooldown_seconds, observation=observation)
            status = 'cancelled' if before == 'cancelling' else outcome.status
            result = outcome.result if status == 'completed' else None
            diagnostic = (outcome.error or status) if status != 'completed' else None
            payload = dict(row['payload'], error=diagnostic)
            repo = JobRepository(session)
            row = repo.update(row['id'], expected_version=row['version'], status=status, result=result,
                              finished_at=utc_now(), error_message=diagnostic, payload=payload,
                              completion_scope=(result or {}).get('completion_scope'), recovery_state=status, **evidence)
            repo.finish_attempt(claim.attempt_id, exit_code=None, result_status=status)
            self._event(session, row, 'job.' + status, before, status, attempt_id=claim.attempt_id, details=evidence)
            if row['batch_id']:
                BatchRepository(session).recompute_counts(row['batch_id'])
            session.get(DeviceRow, claim.assignment.device.device_id).current_job_id = None
            LeaseRepository(session).release(claim.assignment.lease_token, reason=status)
            return self.store._job_from_row(row), True

    def reconcile(self, job_id):
        """Resolve only a proved stopped attempt whose terminal write rolled back."""
        with write_session(self.database) as session:
            repo = JobRepository(session)
            row = repo.get(job_id)
            if row is None:
                raise KeyError(job_id)
            if (row['status'] not in {'running', 'cancelling'} or row['recovery_state'] != 'failed-result-persistence'
                    or not safe_to_reuse(row['process_state'], row['transport_cleanup_state'])):
                raise ApiError('no verified result-persistence hold is eligible for acknowledgment')
            worker_id = row['assigned_worker_id']
            leases = LeaseRepository(session)
            holders = leases.list_active()
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id,
                JobAttempt.attempt_index == row['attempt_count'], JobAttempt.worker_id == worker_id))
            if (len(holders) != 1 or holders[0]['job_id'] != job_id or holders[0]['device_id'] != worker_id
                    or holders[0]['worker_id'] != worker_id or attempt is None or attempt.finished_at is not None):
                raise ApiError('active lease or attempt identity does not match')
            holder = holders[0]
            # Link this hold to its original claim, not just the current device and job IDs.
            hold = row['payload'].get('persistence_hold') or {}
            if hold != {'attempt_id': attempt.attempt_id, 'fence': holder['fencing_version'], 'token': holder['lease_token']}:
                raise ApiError('attempt fencing proof is unavailable')
            before = row['status']
            status = 'cancelled' if before == 'cancelling' else 'failed'
            evidence = {'process_state': row['process_state'], 'transport_cleanup_state': row['transport_cleanup_state']}
            payload = dict(row['payload'])
            payload.pop('persistence_hold')
            row = repo.update(job_id, expected_version=row['version'], status=status, payload=payload,
                              recovery_state='failed-result-persistence-reconciled', finished_at=utc_now(), result=None)
            repo.finish_attempt(attempt.attempt_id, exit_code=None, result_status=status)
            self._event(session, row, 'job.result_reconciled', before, status, attempt_id=attempt.attempt_id, details=evidence)
            if row['batch_id']:
                BatchRepository(session).recompute_counts(row['batch_id'])
            session.get(DeviceRow, worker_id).current_job_id = None
            leases.release(holder['lease_token'], reason='result-persistence-acknowledged')
            return self.store._job_from_row(row), self.blocker(session)
