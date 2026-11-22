"""Scheduler throughput benchmark with the mock executor.

Measures the orchestration path only (claim → lease → executor → complete), not
capture work: the executor returns immediately.  That is exactly what the task
asks for — "使用 Mock Executor 测试调度器" — and it must not be presented as a
real-device number.

    python perf/scheduler_bench.py --jobs 200 --devices 1
    python perf/scheduler_bench.py --jobs 200 --devices 2 --parallel
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from facebook_backend.executors.mock import MockExecutor  # noqa: E402
from facebook_backend.persistence.database import Database  # noqa: E402
from facebook_backend.persistence.models import Base  # noqa: E402
from facebook_backend.persistence.repositories import JobRepository  # noqa: E402
from facebook_backend.scheduler.device_registry import WorkerConfig  # noqa: E402
from facebook_backend.scheduler.manager import Scheduler  # noqa: E402

RESULTS = ROOT / "perf" / "results"


class RecordingExecutor:
    """Zero-latency executor that records when each job started."""

    def __init__(self) -> None:
        self.mock = MockExecutor()
        self.starts: dict[str, tuple[str, float]] = {}

    def execute(self, assignment, *, cancel_event=None):
        self.starts[assignment.job_id] = (assignment.device.device_id, time.monotonic())
        return self.mock.execute(assignment, cancel_event=cancel_event)


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)
    def at(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[index]
    return {
        "p50": round(at(0.50) * 1000, 3),
        "p95": round(at(0.95) * 1000, 3),
        "p99": round(at(0.99) * 1000, 3),
        "max": round(ordered[-1] * 1000, 3),
        "mean": round(statistics.fmean(ordered) * 1000, 3),
    }


def build_workers(count: int) -> list[WorkerConfig]:
    return [
        WorkerConfig(
            worker_id=f"worker-{index:02d}",
            serial=f"emulator-{5554 + index * 2}",
            avd_name=f"avd-{index:02d}",
            capture_port=8081 + index,
            native_port=18766 + index,
            enabled=True,
        )
        for index in range(count)
    ]


def run(jobs: int, devices: int, parallel: bool, label: str) -> dict:
    if not 1 <= jobs <= 200 or not 1 <= devices <= 2:
        raise ValueError("jobs must be 1..200 and devices 1..2")
    with tempfile.TemporaryDirectory() as tmp:
        database = Database.open(f"sqlite+pysqlite:///{(Path(tmp) / 'bench.db').as_posix()}")
        Base.metadata.create_all(database.engine)
        enqueued: dict[str, float] = {}
        try:
            with database.session() as session:
                repo = JobRepository(session)
                for index in range(jobs):
                    job_id = f"fb_bench{index:05d}"
                    repo.create(
                        job_id=job_id,
                        status="queued",
                        payload={"request": {"post_url": "fixture:synthetic"}},
                        run_dir=f"runs/{job_id}",
                    )
                    enqueued[job_id] = time.monotonic()

            executor = RecordingExecutor()
            scheduler = Scheduler(
                database=database,
                devices=build_workers(devices),
                executor=executor,
                max_concurrent_jobs=devices if parallel else 1,
            )
            started = time.monotonic()
            ticks = 0
            while scheduler.queue.depth() > 0 and ticks < jobs * 4 + 100:
                scheduler.tick(parallel=parallel)
                ticks += 1
            elapsed = time.monotonic() - started
            finished = time.monotonic()
            with database.session() as session:
                completed = JobRepository(session).count('completed')
            active_leases = len(scheduler.leases.active_leases())
            scheduler.shutdown()
        finally:
            database.dispose()

    queue_latency = [
        executor.starts[job_id][1] - enqueued[job_id]
        for job_id in enqueued
        if job_id in executor.starts
    ]
    per_device: dict[str, int] = {}
    for device_id, _start in executor.starts.values():
        per_device[device_id] = per_device.get(device_id, 0) + 1
    return {
        "label": label,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "executor": "MockExecutor (no device, no capture)",
        "jobs": jobs,
        "devices": devices,
        "parallel": parallel,
        "completed": completed,
        "started": len(executor.starts),
        "active_leases": active_leases,
        "ticks": ticks,
        "wall_seconds": round(elapsed, 4),
        "throughput_jobs_per_second": round(completed / elapsed, 2) if elapsed else None,
        "queue_latency_ms": percentiles(queue_latency),
        "per_device": per_device,
        "total_span_seconds": round(finished - started, 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduler throughput with a mock executor")
    parser.add_argument("--jobs", type=int, default=200)
    parser.add_argument("--devices", type=int, default=1)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--label", default=None)
    parser.add_argument("--results-dir", default=None, help="default: perf/results")
    args = parser.parse_args(argv)
    label = args.label or ("scheduler_parallel" if args.parallel else "scheduler_serial")
    report = run(args.jobs, args.devices, args.parallel, label)
    results = Path(args.results_dir) if args.results_dir else RESULTS
    results.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = results / f"{label}_{stamp}.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nwritten: {target}")
    return 0 if report["completed"] == report["jobs"] and not report["active_leases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
