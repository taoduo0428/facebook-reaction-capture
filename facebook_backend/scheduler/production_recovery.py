"""Recover the committed single production slot before the manager starts dispatch.

The launch gate consumes the claim's durable pre-spawn proof before handing an
assignment to an executor. After that handoff even verified resource cleanup
leaves the business outcome of a running job uncertain.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from facebook_backend.persistence.lifecycle_events import replay_job_events
from facebook_backend.persistence.models import Device, Job, JobAttempt, JobEvent, utc_now
from facebook_backend.persistence.repositories import BatchRepository, JobRepository, LeaseRepository
from facebook_backend.platform import process_manager
from facebook_backend.scheduler.leases import safe_to_reuse
from facebook_backend.scheduler.queue import write_session
from facebook_backend.scheduler.recovery import ACTIVE_STATUSES


_NEVER_STARTED = ('not-started', 'not-required')
_STOPPED = ('stopped', 'restored')
_CANCEL = 'job.cancellation_requested'
_RECEIPTS = ('job.process_stopped', 'job.cleanup_completed')


def _pair(row):
    return row.get('process_state'), row.get('transport_cleanup_state')


def _consistent_pid(job, attempt, *, never_started):
    """A surviving PID contradicts stop receipts. Absence only corroborates them."""
    identities = {(pid, birth) for pid, birth in ((job['process_pid'], job['process_creation_time']),
                                                  (attempt.process_id, attempt.process_creation_time))
                  if pid is not None or birth is not None}
    if not identities:
        return True
    if never_started or len(identities) != 1:
        return False
    pid, birth = identities.pop()
    return (process_manager.coerce_pid(pid) is not None and process_manager.coerce_creation_time(birth) is not None
            and process_manager.identity_state(pid, birth) == 'absent')


def _review_journal(session, job, attempt, holder):
    history = replay_job_events(session, job['id'])
    if (not history.matches_projection or history.state.attempt_id != attempt.attempt_id
            or history.state.result_write_failed or history.state.attempt_failed):
        return 'attempt journal disagrees with current status or records a failure'
    claim = session.scalar(select(JobEvent).where(JobEvent.job_id == job['id'],
        JobEvent.attempt_id == attempt.attempt_id, JobEvent.event_type == 'job.claimed', JobEvent.event_key.is_not(None)))
    if claim is None:
        return 'current attempt has no typed claim'
    proof = json.loads(claim.payload)
    if (proof.get('attempt') != attempt.attempt_index or proof.get('fencing_version') != holder['fencing_version']
            or _pair(proof) != _NEVER_STARTED):
        return 'claim lacks pre-launch evidence for the current fence'

    # The longest accepted suffix has five events including the claim. Fetch a
    # sixth to detect any extra delivery, even with an unbound attempt id.
    events = list(session.scalars(select(JobEvent).where(JobEvent.job_id == job['id'],
                                  JobEvent.event_id >= claim.event_id).order_by(JobEvent.event_id).limit(6)))
    for event in events:
        details = json.loads(event.payload) if event.event_key is not None else {}
        matches_attempt = (event.attempt_id == attempt.attempt_id
                           or (event.event_type == _CANCEL and event.attempt_id is None))
        if (event.event_key is None or not matches_attempt or event.worker_id != attempt.worker_id
                or event.device_serial != attempt.device_serial or event.batch_id != job['batch_id']
                or details.get('attempt', attempt.attempt_index) != attempt.attempt_index
                or details.get('fencing_version', holder['fencing_version']) != holder['fencing_version']):
            return 'an event after the claim has uncertain ownership'
    sequence = tuple(event.event_type for event in events[1:])
    pair = _pair(job)
    if not safe_to_reuse(*pair):
        return 'process stop or transport cleanup remains unverified'
    if job['status'] == 'running':
        if sequence or pair != _NEVER_STARTED or job['recovery_state'] != 'claimed' or job['cancel_requested_at']:
            return 'prior dispatch may have produced business effects'
    else:
        if not job['cancel_requested_at']:
            return 'cancellation intent has no durable timestamp'
        if pair == _NEVER_STARTED:
            if sequence != (_CANCEL,):
                return 'unstarted cancellation conflicts with dispatch history'
        elif (sequence != ('job.started', _CANCEL, *_RECEIPTS)
              or any(_pair(json.loads(event.payload)) != _STOPPED for event in events[-2:])):
            return 'cancellation lacks stop and cleanup receipts for this attempt'
    if not _consistent_pid(job, attempt, never_started=pair == _NEVER_STARTED):
        return 'persisted process identity contradicts recovery evidence'
    return None


def recover_startup(tx) -> tuple[str | None, str] | None:
    """Return the blocker for the facade, or commit a provable recovery atomically.

    One write reservation covers observation, validation and release. There is no
    reusable plan, race with a later started() or reliance on lease expiry.
    """
    with write_session(tx.database) as session:
        holders = LeaseRepository(session).list_active()
        jobs = list(session.scalars(select(Job.id).where(Job.status.in_(ACTIVE_STATUSES)).limit(2)))
        attempts = list(session.scalars(select(JobAttempt).where(JobAttempt.finished_at.is_(None)).limit(2)))
        occupied = list(session.scalars(select(Device).where(Device.current_job_id.is_not(None)).limit(2)))
        if not any((holders, jobs, attempts, occupied)):
            return None
        if not (len(holders) == len(jobs) == len(attempts) == len(occupied) == 1):
            affected = (jobs[0] if jobs else holders[0]['job_id'] if holders else
                        attempts[0].job_id if attempts else occupied[0].current_job_id)
            return affected, 'restart found inconsistent single-slot ownership counts'
        job_id, attempt, device, holder = jobs[0], attempts[0], occupied[0], holders[0]
        repo = JobRepository(session)
        job = repo.require(job_id)
        latest_index = session.scalar(select(JobAttempt.attempt_index).where(JobAttempt.job_id == job_id)
                                      .order_by(JobAttempt.attempt_index.desc()).limit(1))
        job_owner = (job_id, job['assigned_worker_id'], job['device_serial'], job['attempt_count'])
        attempt_owner = (attempt.job_id, attempt.worker_id, attempt.device_serial, attempt.attempt_index)
        if (job_owner != attempt_owner or latest_index != attempt.attempt_index
                or not (holder['job_id'] == device.current_job_id == job_id)
                or not (holder['device_id'] == holder['worker_id'] == device.id == device.worker_id == attempt.worker_id)
                or device.serial != attempt.device_serial or job['pinned_device_id'] not in {None, device.id}
                or job['capture_run_dir'] != attempt.run_dir or attempt.result_status is not None or attempt.exit_code is not None):
            return job_id, 'current job, attempt, device and lease disagree'
        if (job['recovery_state'] == 'failed-result-persistence' or 'persistence_hold' in job['payload']):
            return job_id, 'result persistence hold requires explicit reconciliation'
        if (job['result'] is not None or job['finished_at'] is not None or job['payload'].get('transport_cleanup_error')):
            return job_id, 'active job has conflicting result or cleanup evidence'
        reason = _review_journal(session, job, attempt, holder)
        if reason:
            return job_id, reason

        # The store's startup integrity gate must succeed before any mutation,
        # including recovery of a standalone job sharing a damaged database.
        tx.store._validate_batch_links(session)
        before = job['status']
        status = 'queued' if before == 'running' else 'cancelled'
        fields = (dict(started_at=None, assigned_worker_id=None, device_serial=None,
                       recovery_state='requeued-after-restart') if status == 'queued' else
                  dict(finished_at=utc_now(), recovery_state='cancelled'))
        updated = repo.update(job_id, expected_version=job['version'], status=status, **fields)
        repo.finish_attempt(attempt.attempt_id, exit_code=None, result_status='requeued' if status == 'queued' else status)
        # A requeued projection clears its assignment. Keep the event associated
        # with the original attempt/worker, using the new projection's version.
        event_context = dict(updated, assigned_worker_id=attempt.worker_id, device_serial=attempt.device_serial)
        tx._event(session, event_context, 'job.recovered', before, status, attempt_id=attempt.attempt_id,
                  details={'attempt': attempt.attempt_index, 'fencing_version': holder['fencing_version'],
                           'process_state': job['process_state'], 'transport_cleanup_state': job['transport_cleanup_state']})
        if job['batch_id']:
            BatchRepository(session).recompute_counts(job['batch_id'])
        device.current_job_id = None
        LeaseRepository(session).release(holder['lease_token'], reason='restart-recovery')
        return None
