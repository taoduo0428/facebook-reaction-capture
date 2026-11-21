"""Device-aware scheduler.

Composition:

    Dispatcher (decision)  +  JobQueue (CAS claim)  +  LeaseManager (exclusivity)
                            +  ExecutorPort (the work)

Properties this class is responsible for, each covered by a test:

* **Serial by default** — ``max_concurrent_jobs=1`` means one capture at a time,
  exactly like the current primary/standby runtime.
* **One job per device** — a device is only dispatched when it holds no slot and
  no active lease; the database makes a second active lease impossible.
* **Rate limit stays on the device** — the job is pinned and the device cools
  down; it is never re-dispatched to another account/device.
* **Device/executor failures may fail over** — only after the executor reports
  the failure class; the old device cools down.
* **No fabricated stops** — ``process_state`` comes from the executor, never
  from the scheduler.
* **Graceful shutdown** — ``shutdown()`` stops accepting new work and waits for
  in-flight executions; it never kills a process (Phase 5 adds the drain policy).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence

from facebook_backend.executors.base import ExecutionOutcome, ExecutorPort
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.repositories import DeviceRepository, EventRepository
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.dispatcher import DispatchPlan, Dispatcher
from facebook_backend.scheduler.failures import FailureClass, RetryPolicy, classify_failure
from facebook_backend.scheduler.leases import LeaseHeldError, LeaseManager
from facebook_backend.scheduler.models import (
    Device,
    ExecutionSlot,
    SchedulerSnapshot,
    WorkerAssignment,
    WorkerHealth,
)
from facebook_backend.scheduler.queue import JobQueue

LEASE_HELD_COOLDOWN_SECONDS = 30


def _request_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Extract the normalised request, refusing a malformed payload.

    A job whose payload has no ``request`` object cannot be executed; raising
    here (inside the executor try-block) turns it into a classified failure and
    still releases the lease instead of crashing the scheduler loop.
    """
    payload = job.get("payload") or {}
    request = payload.get("request") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        raise ValueError("job payload has no request object")
    return dict(request)


class SchedulerClosedError(RuntimeError):
    """The scheduler was shut down and no longer accepts work."""


class Scheduler:
    def __init__(
        self,
        *,
        database: Database,
        devices: Iterable[WorkerConfig],
        executor: ExecutorPort,
        max_concurrent_jobs: int = 1,
        lease_ttl_seconds: int = 900,
        retry_policy: RetryPolicy | None = None,
        require_healthy_device: bool = False,
        health_probe: Callable[[WorkerConfig], WorkerHealth] | None = None,
        clock: Callable[[], float] | None = None,
        queue: JobQueue | None = None,
        leases: LeaseManager | None = None,
        dispatcher: Dispatcher | None = None,
    ):
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be at least 1")
        self.database = database
        self.executor = executor
        self.max_concurrent_jobs = max_concurrent_jobs
        self.clock = clock or time.time
        self.queue = queue or JobQueue(database, clock=self.clock)
        self.leases = leases or LeaseManager(database, ttl_seconds=lease_ttl_seconds, clock=self.clock)
        self.dispatcher = dispatcher or Dispatcher(
            max_concurrent_jobs=max_concurrent_jobs,
            require_healthy_device=require_healthy_device,
            clock=self.clock,
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self.health_probe = health_probe
        self._configs: dict[str, WorkerConfig] = {worker.worker_id: worker for worker in devices}
        self._health: dict[str, WorkerHealth] = {}
        self._cooldowns: dict[str, float] = {}
        self._slots: dict[str, ExecutionSlot] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._inflight = 0
        self.sync_devices()

    # --- configuration -------------------------------------------------
    def sync_devices(self) -> None:
        """Mirror the worker configuration into the ``devices`` table.

        Leases reference ``devices.id``, so a device must exist before a lease
        can be taken.  The JSON configuration stays the source of truth; this is
        the registry projection the scheduler and the audit trail read.
        """
        with self.database.session() as session:
            repo = DeviceRepository(session)
            for worker in self._configs.values():
                repo.upsert(
                    device_id=worker.worker_id,
                    worker_id=worker.worker_id,
                    serial=worker.serial,
                    avd_name=worker.avd_name,
                    capture_port=worker.capture_port,
                    native_port=worker.native_port,
                    enabled=worker.enabled,
                )

    def update_devices(self, devices: Iterable[WorkerConfig]) -> None:
        with self._lock:
            self._configs = {worker.worker_id: worker for worker in devices}
        self.sync_devices()

    def refresh_health(self) -> dict[str, WorkerHealth]:
        if self.health_probe is None:
            return {}
        with self._lock:
            workers = list(self._configs.values())
        for worker in workers:
            try:
                health = self.health_probe(worker)
            except Exception:  # noqa: BLE001 - a probe failure is "unknown", not fatal
                health = WorkerHealth(device_id=worker.worker_id, state="unknown")
            with self._lock:
                self._health[worker.worker_id] = health
        return dict(self._health)

    def devices(self, *, now: float | None = None) -> list[Device]:
        moment = now if now is not None else self.clock()
        with self._lock:
            configs = list(self._configs.values())
            slots = dict(self._slots)
            cooldowns = dict(self._cooldowns)
            health = dict(self._health)
        return [
            Device.from_worker_config(
                worker,
                health=health.get(worker.worker_id),
                current_job_id=slots[worker.worker_id].job_id if worker.worker_id in slots else None,
                cooldown_until=cooldowns.get(worker.worker_id, 0.0),
            )
            for worker in configs
        ]

    def snapshot(self, *, now: float | None = None) -> SchedulerSnapshot:
        moment = now if now is not None else self.clock()
        devices = self.devices(now=moment)
        leases = {lease["device_id"]: lease for lease in self.leases.active_leases()}
        plan = self.dispatcher.plan(
            devices,
            active_leases=leases,
            queue_depth=self.queue.depth(),
            now=moment,
        )
        with self._lock:
            slots = tuple(self._slots.values())
        return SchedulerSnapshot(
            max_concurrent_jobs=self.max_concurrent_jobs,
            devices=tuple(devices),
            slots=slots,
            queue_depth=self.queue.depth(),
            blocked_reasons=dict(plan.blocked),
        )

    # --- main loop step ------------------------------------------------
    def tick(self, *, parallel: bool = False) -> list[ExecutionOutcome]:
        """Plan and run at most ``max_concurrent_jobs`` assignments."""
        if self._closed:
            raise SchedulerClosedError("scheduler is shut down")
        now = self.clock()
        devices = self.devices(now=now)
        leases = {lease["device_id"]: lease for lease in self.leases.active_leases()}
        plan: DispatchPlan = self.dispatcher.plan(
            devices,
            active_leases=leases,
            queue_depth=self.queue.depth(),
            now=now,
        )
        if not plan.assignments:
            return []

        assignments: list[tuple[Device, dict[str, Any]]] = []
        for device in plan.assignments:
            job = self.queue.claim(
                device_id=device.device_id,
                worker_id=device.worker_id,
                device_serial=device.serial,
            )
            if job is None:
                break
            assignments.append((device, job))
        if not assignments:
            return []

        if parallel and len(assignments) > 1:
            with ThreadPoolExecutor(max_workers=len(assignments)) as pool:
                futures = [pool.submit(self._run_one, device, job) for device, job in assignments]
                return [future.result() for future in futures]
        return [self._run_one(device, job) for device, job in assignments]

    # --- one assignment ------------------------------------------------
    def _run_one(self, device: Device, job: dict[str, Any]) -> ExecutionOutcome:
        job_id = job["id"]
        now = self.clock()
        with self._lock:
            self._inflight += 1
        try:
            try:
                lease = self.leases.acquire(
                    device.device_id,
                    job_id=job_id,
                    worker_id=device.worker_id,
                    ttl_seconds=self.leases.ttl_seconds,
                    now=now,
                )
            except LeaseHeldError as exc:
                # The device is not usable right now: give the job back
                # untouched (it keeps its attempt count) and cool the device.
                self.queue.release(
                    job_id,
                    reason="lease-held",
                    error=str(exc),
                    recovery_state="queued-lease-held",
                )
                with self._lock:
                    self._cooldowns[device.device_id] = now + LEASE_HELD_COOLDOWN_SECONDS
                return ExecutionOutcome(
                    job_id=job_id,
                    status="failed",
                    failure=FailureClass.UNKNOWN,
                    error=f"lease-held: {exc}",
                )

            slot = ExecutionSlot(
                device_id=device.device_id,
                worker_id=device.worker_id,
                job_id=job_id,
                lease_token=lease["lease_token"],
                fencing_version=lease["fencing_version"],
                started_at=lease["acquired_at"],
            )
            with self._lock:
                self._slots[device.device_id] = slot
            self._event(
                "dispatched",
                job_id,
                {
                    "device_id": device.device_id,
                    "worker_id": device.worker_id,
                    "serial": device.serial,
                    "attempt": job["attempt_count"],
                    "lease_token": lease["lease_token"],
                    "fencing_version": lease["fencing_version"],
                },
            )
            try:
                assignment = WorkerAssignment(
                    job_id=job_id,
                    device=device,
                    request=_request_payload(job),
                    run_dir=job.get("run_dir"),
                    attempt=int(job.get("attempt_count") or 0),
                    lease_token=lease["lease_token"],
                    fencing_version=lease["fencing_version"],
                )
                outcome = self.executor.execute(assignment)
            except BaseException as exc:  # noqa: BLE001 - an executor crash is a failure
                outcome = ExecutionOutcome(
                    job_id=job_id,
                    status="failed",
                    failure=classify_failure(exc),
                    error=f"{type(exc).__name__}: {exc}",
                    process_state=None,
                )
            finally:
                with self._lock:
                    self._slots.pop(device.device_id, None)
                self._safe_release(lease["lease_token"], reason=outcome_status(outcome))
            self._apply_outcome(device, job, outcome)
            return outcome
        finally:
            with self._lock:
                self._inflight -= 1

    def _apply_outcome(
        self, device: Device, job: dict[str, Any], outcome: ExecutionOutcome
    ) -> None:
        job_id = outcome.job_id
        if outcome.status == "completed":
            self.queue.complete(
                job_id,
                status="completed",
                result=outcome.result,
                process_state=outcome.process_state,
            )
            self.queue.clear_pin(job_id)
            self._event("completed", job_id, {"device_id": device.device_id})
            return
        if outcome.status == "cancelled":
            self.queue.complete(
                job_id,
                status="cancelled",
                error=outcome.error,
                process_state=outcome.process_state,
            )
            self._event("cancelled", job_id, {"device_id": device.device_id})
            return

        failure = outcome.failure or FailureClass.UNKNOWN
        now = self.clock()
        decision = self.retry_policy.decide(
            failure,
            attempt=int(job.get("attempt_count") or 1),
            device_cooldown_seconds=self._cooldown_for(device),
        )
        if decision.cooldown_seconds:
            with self._lock:
                self._cooldowns[device.device_id] = now + decision.cooldown_seconds
        if decision.retry:
            self.queue.release(
                job_id,
                reason=failure.value,
                error=outcome.error,
                pinned_device_id=device.device_id if decision.same_device else None,
                recovery_state=f"retry-{failure.value}",
            )
            self._event(
                "retry-scheduled",
                job_id,
                {
                    "device_id": device.device_id,
                    "failure": failure.value,
                    "same_device": decision.same_device,
                    "cooldown_seconds": decision.cooldown_seconds,
                    "reason": decision.reason,
                },
            )
            return
        self.queue.complete(
            job_id,
            status="failed",
            error=outcome.error,
            process_state=outcome.process_state,
        )
        self._event(
            "failed",
            job_id,
            {"device_id": device.device_id, "failure": failure.value, "reason": decision.reason},
        )

    # --- helpers -------------------------------------------------------
    def _cooldown_for(self, device: Device) -> int:
        with self._lock:
            config = self._configs.get(device.worker_id)
        return int(config.cooldown_seconds) if config else 900

    def _safe_release(self, lease_token: str, *, reason: str) -> None:
        try:
            self.leases.release(lease_token, reason=reason)
        except LeaseHeldError:
            # Losing the lease here must not mask the execution outcome; the
            # device simply stays blocked until recovery verifies it.
            pass

    def _event(self, event_type: str, job_id: str, payload: dict[str, Any]) -> None:
        try:
            with self.database.session() as session:
                EventRepository(session).append(
                    event_type=event_type, job_id=job_id, payload=payload
                )
        except Exception:  # noqa: BLE001 - telemetry must not break execution
            pass

    def heartbeat(self) -> None:
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            try:
                self.leases.heartbeat(slot.lease_token)
            except LeaseHeldError:
                pass

    def shutdown(self, *, wait_seconds: float = 0.0) -> None:
        """Stop accepting work; optionally wait for in-flight executions."""
        self._closed = True
        deadline = self.clock() + max(0.0, wait_seconds)
        while wait_seconds and self.clock() < deadline:
            with self._lock:
                if self._inflight == 0:
                    break
            time.sleep(0.05)

    @property
    def closed(self) -> bool:
        return self._closed


def outcome_status(outcome: ExecutionOutcome) -> str:
    return "completed" if outcome.status == "completed" else outcome.status


__all__ = ["Scheduler", "SchedulerClosedError"]
