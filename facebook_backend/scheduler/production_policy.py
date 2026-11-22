"""Durable retry and takeover decisions for the one production execution slot."""
from __future__ import annotations

from dataclasses import dataclass
import time

from facebook_backend.persistence.models import Device, utc_now
from facebook_backend.persistence.repositories import BatchRepository, DeviceRepository, JobRepository, LeaseRepository
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.failures import FailureClass, RetryPolicy


_POLICY = RetryPolicy()
_FAILED = 'policy-failed'
_ACTIVE = 'policy-active'
_STOPPED = ('stopped', 'restored')


@dataclass(frozen=True)
class DeviceFailure:
    attempt_id: str
    worker_id: str
    successor: WorkerConfig | None
    checkpoint: dict | None


def _snapshot(serial):
    # Use the same device/boot diagnostics as the legacy worker. A proxy
    # timeout can coexist with a positively verified booted device.
    from facebook_capture_api import adb_snapshot
    return adb_snapshot(serial, timeout_seconds=1.0)


def _offline(serial):
    for index in range(3):
        state = _snapshot(serial)
        if not (state.get('state') == 'offline' or
                (state.get('state') == 'device' and state.get('boot_completed') is False)):
            return False  # Online, or missing/indeterminate host diagnostics.
        if index < 2:
            time.sleep(0.25)
    return True


def observe_device_failure(manager, claim, outcome):
    """Probe only after resource proof, outside the SQL write reservation."""
    assignment = claim.assignment
    if (outcome.status != 'failed' or outcome.failure != FailureClass.DEVICE or
            outcome.job_id != assignment.job_id or outcome.attempt_id not in (None, claim.attempt_id) or
            (outcome.process_state, outcome.transport_cleanup_state) != _STOPPED):
        return None
    source = assignment.device
    with manager._lock:
        if manager._jobs[assignment.job_id]['status'] != 'running' or manager._capture_lease_uncertain:
            return None
        start = manager._worker_order.index(source.worker_id) + 1
        candidates = [manager.workers[key] for key in manager._worker_order[start:]
                      if manager.workers[key].enabled and key not in manager._failed_worker_ids
                      and key not in manager._orphaned_worker_ids
                      and (manager._worker_runtime[key]['cooldown_until'] or 0) <= time.time()]
    if not _offline(source.serial):
        return None
    successor = None
    for candidate in candidates:
        state = _snapshot(candidate.serial)
        if state.get('state') == 'device' and state.get('boot_completed') is True:
            successor = candidate
            break
    # Preserve partial rows under the existing checkpoint/materialization rules.
    # Only the finish transaction publishes the checkpoint and authorizes retry.
    checkpoint = manager._checkpoint_job_locked(dict(claim.job), worker_id=source.worker_id, serial=source.serial)
    return DeviceFailure(claim.attempt_id, source.worker_id, successor, checkpoint)


def restore_policy(manager, session, *, recheck_standby=False):
    """Project committed device health/cooldowns into the facade after finish or restart."""
    rows = DeviceRepository(session).list()
    failed = {d['worker_id'] for d in rows if d['health_status'] == _FAILED}
    promoted = [d for d in rows if d['health_status'] == _ACTIVE]
    manager._failed_worker_ids.update(failed)
    for device in rows:
        if device['worker_id'] in manager._worker_runtime:
            runtime = manager._worker_runtime[device['worker_id']]
            runtime['cooldown_until'] = device['cooldown_until'] or 0
            if device['health_status'] == _FAILED:
                runtime.update(last_failure='confirmed device failure', last_failure_at=device['updated_at'])
    if not failed and not promoted:
        return
    selected = None
    if len(promoted) == 1:
        target = promoted[0]
        worker = manager.workers.get(target['worker_id'])
        if (worker and worker.enabled and worker.serial == target['serial'] and worker.worker_id not in
                manager._failed_worker_ids | manager._orphaned_worker_ids):
            if recheck_standby:
                state = _snapshot(worker.serial)
                if state.get('state') != 'device' or state.get('boot_completed') is not True:
                    manager._active_worker_id = None
                    manager._started_worker_ids.clear()
                    return
            selected = worker.worker_id
            source = (target['capabilities'] or {}).get('failover_from')
            manager._last_failover = dict(from_worker_id=source, to_worker_id=selected,
                                          at=(target['capabilities'] or {}).get('failover_at'))
    manager._active_worker_id = selected
    manager._started_worker_ids = {selected} if selected else set()


def _promote(session, failed, successor, now):
    failed.health_status = _FAILED
    failed.updated_at = utc_now()
    if successor is None:
        return None
    repo = DeviceRepository(session)
    old = repo.get(successor.worker_id)
    if old and (old['serial'] != successor.serial or old['health_status'] == _FAILED or old['current_job_id']
                or (old['cooldown_until'] or 0) > now):
        return None
    capabilities = dict((old or {}).get('capabilities') or {}, failover_from=failed.worker_id,
                        failover_at=failed.updated_at)
    repo.upsert(device_id=successor.worker_id, worker_id=successor.worker_id, serial=successor.serial,
                enabled=successor.enabled, avd_name=successor.avd_name, capture_port=successor.capture_port,
                native_port=successor.native_port, health_status=_ACTIVE, capabilities=capabilities)
    return successor.worker_id


def settle_failure(tx, session, row, claim, outcome, *, cooldown_seconds, observation=None):
    """Finish the fenced failed attempt in the transaction that owns its two proof events."""
    source = claim.assignment.device
    device = session.get(Device, source.device_id)
    now = time.time()
    failure = FailureClass(outcome.failure or FailureClass.UNKNOWN)
    attempt = row['attempt_count']
    stopped = (outcome.process_state, outcome.transport_cleanup_state) == _STOPPED
    pin = row['pinned_device_id']
    payload = dict(row['payload'], error=outcome.error or failure.value,
                   last_worker_id=source.worker_id, last_serial=source.serial)
    # Preserve the original job budget across restarts and policy upgrades.
    limit = payload.setdefault('max_attempts', _POLICY.max_attempts)
    if type(limit) is not int or limit < 1:
        raise ValueError('invalid persisted attempt limit')
    attempt_limit = min(limit, _POLICY.max_attempts)
    evidence = dict(process_state=outcome.process_state, transport_cleanup_state=outcome.transport_cleanup_state,
                    failure=failure.value, attempt=attempt)
    fields = dict(status='failed', recovery_state='failed', finished_at=utc_now(), result=None, completion_scope=None)
    event = 'job.failed'
    cooldown = 0
    if failure is FailureClass.RATE_LIMIT:
        # A limit terminates this job. Even independent queued work waits for the
        # current device. A new manager reads the same absolute cooldown deadline.
        pin = source.device_id
        cooldown = cooldown_seconds
    elif failure is FailureClass.NETWORK and stopped and attempt < attempt_limit:
        pin = source.device_id
        cooldown = _POLICY.network_backoff_seconds[min(attempt - 1, len(_POLICY.network_backoff_seconds) - 1)]
        event = 'job.retry_scheduled'
        fields.update(status='queued', recovery_state='network-backoff', finished_at=None)
        evidence['same_device'] = True
    elif failure is FailureClass.DEVICE and stopped and observation is not None:
        if (observation.attempt_id, observation.worker_id) != (claim.attempt_id, source.worker_id):
            raise ValueError('device failure evidence belongs to another attempt')
        successor = _promote(session, device, observation.successor, now)
        payload['checkpoint'] = observation.checkpoint
        fields['recovery_state'] = 'confirmed-device-failure'
        if successor and pin is None and row['failover_count'] == 0 and attempt < attempt_limit:
            pin = successor
            event = 'job.failover'
            fields.update(status='queued', recovery_state='checkpointed-queued-for-standby',
                          failover_count=1, finished_at=None)
            evidence['same_device'] = False
    if cooldown:
        device.cooldown_until = max(device.cooldown_until or 0, now + cooldown)
        evidence['cooldown_seconds'] = cooldown
    repo = JobRepository(session)
    repo.finish_attempt(claim.attempt_id, exit_code=None, result_status='failed')
    if fields['status'] == 'queued':
        fields.update(assigned_worker_id=None, device_serial=None, started_at=None)
    updated = repo.update(row['id'], expected_version=row['version'], pinned_device_id=pin,
                          payload=payload, error_message=payload['error'], process_state=outcome.process_state,
                          transport_cleanup_state=outcome.transport_cleanup_state, **fields)
    event_row = dict(updated, assigned_worker_id=source.worker_id, device_serial=source.serial)
    tx._event(session, event_row, event, 'running', updated['status'], attempt_id=claim.attempt_id, details=evidence)
    if row['batch_id']:
        BatchRepository(session).recompute_counts(row['batch_id'])
    device.current_job_id = None
    LeaseRepository(session).release(claim.assignment.lease_token, reason=event)
    return tx.store._job_from_row(updated), True
