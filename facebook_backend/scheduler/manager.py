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

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from facebook_backend.executors.base import ExecutionOutcome, ExecutorPort
from facebook_backend.observability.context import bound
from facebook_backend.observability.logging import get_logger, log_event
from facebook_backend.observability.metrics import METRICS
from facebook_backend.observability.tracing import span
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import utc_now
from facebook_backend.persistence.repositories import DeviceRepository, EventRepository, JobRepository, LeaseRepository
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.dispatcher import DispatchPlan, Dispatcher
from facebook_backend.scheduler.failures import FailureClass, RetryPolicy, classify_failure
from facebook_backend.scheduler.leases import LeaseHeldError, LeaseManager, safe_to_reuse
from facebook_backend.scheduler.models import (
    Device,
    ExecutionSlot,
    SchedulerSnapshot,
    WorkerAssignment,
    WorkerHealth,
)
from facebook_backend.scheduler.queue import JobQueue, write_session

LEASE_HELD_COOLDOWN_SECONDS = 30

LOGGER = get_logger("scheduler")


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
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        # One pool for the scheduler's lifetime: creating a ThreadPoolExecutor per
        # tick made the parallel path slower than the serial one (measured in
        # perf/scheduler_bench.py), because pool startup dominated the work.
        self._pool: ThreadPoolExecutor | None = None
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

    # --- cancellation ---------------------------------------------------
    def request_cancel(self, job_id: str) -> dict[str, Any]:
        """Cooperative cancellation.

        * ``queued`` — cancelled immediately: nothing was started.
        * ``running`` — becomes ``cancelling`` and the executor's cancel event is
          set.  The job only becomes ``cancelled`` when the executor reports a
          proven stop; otherwise it stays ``cancelling`` (see ``_apply_outcome``).
        * anything else — returned unchanged (idempotent).
        """
        with self._lock:
            job = self.queue.request_cancel(job_id)
            if job["status"] == "cancelling":
                self._cancel_events.setdefault(job_id, threading.Event()).set()
        return job

    # --- main loop step ------------------------------------------------
    def tick(self, *, parallel: bool = False) -> list[ExecutionOutcome]:
        """Plan and run at most ``max_concurrent_jobs`` assignments."""
        if self._closed:
            raise SchedulerClosedError("scheduler is shut down")
        METRICS.scheduler_ticks.inc()
        now = self.clock()
        devices = self.devices(now=now)
        leases = {lease["device_id"]: lease for lease in self.leases.active_leases()}
        depth = self.queue.depth()
        METRICS.queue_depth.set(depth)
        plan: DispatchPlan = self.dispatcher.plan(
            devices,
            active_leases=leases,
            queue_depth=depth,
            now=now,
        )
        self._publish_device_metrics(devices, plan, now=now)
        if not plan.assignments:
            METRICS.queue_depth.set(self.queue.depth())
            return []

        assignments = []
        for device in plan.assignments:
            claimed = self.queue.claim_for_dispatch(device, capacity=self.max_concurrent_jobs,
                                                    ttl=self.leases.ttl_seconds)
            if claimed is not None:
                assignments.append((device, *claimed))
        if not assignments:
            METRICS.queue_depth.set(self.queue.depth())
            return []

        if parallel and len(assignments) > 1:
            pool = self._executor_pool()
            futures = [pool.submit(self._run_one, *assignment) for assignment in assignments]
            outcomes = [future.result() for future in futures]
            METRICS.queue_depth.set(self.queue.depth())
            return outcomes
        outcomes = [self._run_one(*assignment) for assignment in assignments]
        METRICS.queue_depth.set(self.queue.depth())
        return outcomes

    # --- one assignment ------------------------------------------------
    def _run_one(self, device: Device, job: dict[str, Any], lease: dict[str, Any]) -> ExecutionOutcome:
        job_id = job["id"]
        now = self.clock()
        with self._lock:
            self._inflight += 1
        try:
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
                cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
                if self.queue.get(job_id)["status"] == "cancelling":
                    cancel_event.set()
            started = self.clock()
            with bound(
                job_id=job_id,
                worker_id=device.worker_id,
                device_serial=device.serial,
                attempt_id=str(job["attempt_count"]),
            ):
                with span(
                    "job.execute",
                    device_id=device.device_id,
                    worker_id=device.worker_id,
                    attempt=job["attempt_count"],
                ):
                    entered_executor = False
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
                        if cancel_event.is_set():
                            outcome = ExecutionOutcome(job_id, "cancelled", process_state="not-started",
                                                       transport_cleanup_state="not-required")
                        else:
                            entered_executor = True
                            outcome = self.executor.execute(assignment, cancel_event=cancel_event)
                            if not isinstance(outcome, ExecutionOutcome) or outcome.job_id != job_id:
                                raise ValueError("executor returned an outcome for a different assignment")
                    except BaseException as exc:  # noqa: BLE001 - an executor crash is a failure
                        outcome = ExecutionOutcome(
                            job_id=job_id,
                            status="failed",
                            failure=classify_failure(exc),
                            error=f"{type(exc).__name__}: {exc}",
                            process_state=None if entered_executor else "not-started",
                            transport_cleanup_state=None if entered_executor else "not-required",
                        )
                elapsed = max(0.0, self.clock() - started)
                METRICS.job_execution.observe(
                    elapsed, device=device.device_id, status=outcome.status
                )
                self._observe_queue_wait(job, now=now)
                self._apply_outcome(device, job, lease, outcome)
            return outcome
        finally:
            with self._lock:
                self._inflight -= 1
                self._slots.pop(device.device_id, None)
                self._cancel_events.pop(job_id, None)

    def _apply_outcome(self, device, job, lease, outcome):
        job_id = job["id"]
        failure = outcome.failure or FailureClass.UNKNOWN
        decision = self.retry_policy.decide(failure, attempt=job["attempt_count"],
                                            device_cooldown_seconds=self._cooldown_for(device))
        safe = safe_to_reuse(outcome.process_state, outcome.transport_cleanup_state)
        with write_session(self.database) as session:
            jobs, leases = JobRepository(session), LeaseRepository(session)
            current = jobs.require(job_id)
            holder = leases.active(device.device_id)
            if (not holder or holder["lease_token"] != lease["lease_token"]
                    or holder["job_id"] != job_id or current["attempt_count"] != job["attempt_count"]
                    or current["status"] not in {"running", "cancelling"}):
                raise LeaseHeldError("assignment no longer owns this job and lease")
            cancelled = current["status"] == "cancelling" or outcome.status == "cancelled"
            fields = dict(process_state=outcome.process_state,
                          transport_cleanup_state=outcome.transport_cleanup_state,
                          error_message=outcome.error)
            payload = {"device_id": device.device_id}
            if not safe:
                event = "cancel-unverified" if cancelled else "execution-unverified"
                fields.update(status="cancelling" if cancelled else "running",
                              recovery_state="cancel-failed-stop-unverified" if cancelled else event,
                              pinned_device_id=current["pinned_device_id"] or device.device_id)
            elif cancelled:
                event = "cancelled"
                fields.update(status=event, finished_at=utc_now(), recovery_state=event)
            elif outcome.status == "failed" and decision.retry:
                event = "retry-scheduled"
                fields.update(status="queued", started_at=None, finished_at=None,
                              assigned_worker_id=None, device_serial=None,
                              recovery_state=f"retry-{failure.value}",
                              pinned_device_id=current["pinned_device_id"] or (
                                  device.device_id if decision.same_device else None))
                payload.update(failure=failure.value, same_device=bool(fields["pinned_device_id"]),
                               cooldown_seconds=decision.cooldown_seconds, reason=decision.reason)
            else:
                event = outcome.status
                fields.update(status=event, result=outcome.result, finished_at=utc_now(), recovery_state=event)
                if event == "completed":
                    fields["pinned_device_id"] = None
            jobs.update(job_id, expected_version=current["version"], **fields)
            EventRepository(session).append(event_type=event, job_id=job_id, payload=payload)
            if safe:
                leases.release(lease["lease_token"], reason=event)

        if event == "retry-scheduled":
            with self._lock:
                self._cooldowns[device.device_id] = self.clock() + decision.cooldown_seconds
            METRICS.retries.inc(failure=failure.value)
            if not payload["same_device"]:
                METRICS.failovers.inc()
        elif event in {"completed", "failed", "cancelled"}:
            METRICS.jobs_completed.inc(status=event)
        if cancelled:
            METRICS.cancellations.inc(outcome="verified-stop" if safe else "unverified")
        log_event(LOGGER, "job." + event.replace("-", "_"), job_id=job_id, **payload)

    # --- helpers -------------------------------------------------------
    def _executor_pool(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self.max_concurrent_jobs,
                    thread_name_prefix="facebook-scheduler",
                )
            return self._pool

    def _observe_queue_wait(self, job: dict[str, Any], *, now: float) -> None:
        created = job.get("created_at")
        if not created:
            return
        try:
            parsed = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            return
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        METRICS.queue_wait.observe(max(0.0, now - parsed.timestamp()))

    def _publish_device_metrics(self, devices: Sequence[Device], plan: Any, *, now: float) -> None:
        """Count devices by intrinsic state.

        ``concurrency-limit`` is deliberately **not** "blocked": the device is
        healthy and free, it simply was not needed in this tick.  Only real
        obstacles (lease, health, resource conflict) count as blocked.
        """
        states = {"available": 0, "busy": 0, "cooldown": 0, "disabled": 0, "blocked": 0}
        for device in devices:
            reason = plan.blocked.get(device.device_id, "")
            if not device.enabled:
                states["disabled"] += 1
            elif device.current_job_id is not None:
                states["busy"] += 1
            elif float(device.cooldown_until or 0.0) > now:
                states["cooldown"] += 1
            elif reason.startswith(("lease-held", "unhealthy", "resource-conflict")):
                states["blocked"] += 1
            else:
                states["available"] += 1
        for state, value in states.items():
            METRICS.devices.set(value, state=state)

    def _cooldown_for(self, device: Device) -> int:
        with self._lock:
            config = self._configs.get(device.worker_id)
        return int(config.cooldown_seconds) if config else 900

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
        with self._lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=False)

    @property
    def closed(self) -> bool:
        return self._closed


def outcome_status(outcome: ExecutionOutcome) -> str:
    return "completed" if outcome.status == "completed" else outcome.status


__all__ = ["Scheduler", "SchedulerClosedError"]
