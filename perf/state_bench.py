"""Phase K store, queue, contention, recovery and streaming hash fixtures."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from facebook_backend.persistence.database import Database, sqlite_pragmas
from facebook_backend.persistence.job_store import DatabaseJobStore
from facebook_backend.persistence.legacy_manifest_import import _collect_artifacts
from facebook_backend.persistence.repositories import DeviceRepository, JobRepository, LeaseRepository
from facebook_backend.scheduler.models import Device
from facebook_backend.scheduler.queue import JobQueue
from facebook_backend.scheduler.recovery import RecoveryPlanner
from perf.evidence import Resources, metadata, publish, quantiles_ms
from perf.scheduler_bench import build_workers, run as run_queue


def timed(call, *args, **kwargs):
    start = perf_counter()
    result = call(*args, **kwargs)
    return perf_counter() - start, result


def stats(samples, elapsed):
    return {'count': len(samples), 'wall_seconds': round(elapsed, 6),
            'ops_per_second': round(len(samples) / elapsed, 3), 'ms': quantiles_ms(samples)}


def store_bench(db):
    store = DatabaseJobStore(db)
    barrier = threading.Barrier(2, timeout=3)
    def writer(lane):
        samples = []
        for i in range(16):
            barrier.wait()
            t, _ = timed(store.save_job, {'job_id': f'fb_store{lane}{i:03}', 'status': 'completed'})
            samples.append(t)
        return samples

    start = perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        samples = [t for lane in pool.map(writer, range(2)) for t in lane]
    writes = stats(samples, perf_counter() - start)
    read_times = []
    expected = {f'fb_store{lane}{i:03}' for lane in range(2) for i in range(16)}
    for _ in range(8):
        t, rows = timed(lambda: list(store.load_job_manifests()))
        if {key for _, key in rows} != expected or len(rows) != len(expected):
            raise RuntimeError('store readback differs from acknowledged writes')
        read_times.append(t)
    return {'competing_writers': 2, 'writes': writes, 'full_reads': stats(read_times, sum(read_times)),
            'verified_rows_per_read': len(expected)}


def contention_recovery(db):
    device = Device.from_worker_config(build_workers(1)[0])
    with db.session() as session:
        DeviceRepository(session).upsert(device_id=device.device_id, worker_id=device.worker_id, serial=device.serial)
    queue = JobQueue(db)
    planner = RecoveryPlanner(db, identity_probe=lambda *_: 'unknown')
    accepted, refused, blocked_times, recovered_times = [], [], [], []
    def contender(sync):
        sync.wait(timeout=3)
        return timed(queue.claim_for_dispatch, device, capacity=1, ttl=60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for index in range(12):
            key = f'fb_race{index:04}'
            with db.session() as session:
                JobRepository(session).create(job_id=key, status='queued', payload={})
            barrier = threading.Barrier(2)
            pair = [pool.submit(contender, barrier) for _ in range(2)]
            results = [f.result() for f in pair]
            winners = [(t, r) for t, r in results if r is not None]
            if len(winners) != 1 or winners[0][1][0]['id'] != key:
                raise RuntimeError('expected one lease owner for this job')
            accepted.append(winners[0][0])
            refused.extend(t for t, result in results if result is None)
            # A simulated crash immediately after dispatch has no cleanup evidence.
            t, blocked = timed(planner.run)
            with db.session() as session:
                holders = LeaseRepository(session).list_active()
            if blocked.blocked != (key,) or len(holders) != 1 or holders[0]['job_id'] != key:
                raise RuntimeError('unverified holder was not retained')
            blocked_times.append(t)
            with db.session() as session:
                JobRepository(session).update(key, status='cancelling', process_state='stopped', transport_cleanup_state='restored')
            t, recovered = timed(planner.run)
            with db.session() as session:
                if (recovered.cancelled != (key,) or recovered.released_leases != (holders[0]['lease_token'],)
                        or JobRepository(session).require(key)['status'] != 'cancelled' or LeaseRepository(session).list_active()):
                    raise RuntimeError('recovery did not commit job cancellation and lease release')
            recovered_times.append(t)
    return {'rounds': 12, 'contenders': 2, 'acquired': len(accepted), 'held': len(refused),
            'acquire_ms': quantiles_ms(accepted), 'refusal_ms': quantiles_ms(refused),
            'recovery_block': stats(blocked_times, sum(blocked_times)),
            'recovery_cancel_release': stats(recovered_times, sum(recovered_times))}


def hash_bench(root):
    run_dir = root / 'synthetic'
    run_dir.mkdir()
    path = run_dir / 'raw-network.pcap'
    block = bytes(range(256)) * 4096
    expected = hashlib.sha256()
    with path.open('xb') as f:
        for _ in range(4):
            f.write(block)
            expected.update(block)
    samples = []
    size = path.stat().st_size
    for _ in range(8):
        t, (entries, _) = timed(_collect_artifacts, root, run_dir.name)
        if len(entries) != 1 or entries[0].size != size or entries[0].sha256 != expected.hexdigest():
            raise RuntimeError('streaming hash disagrees with fixture digest')
        samples.append(t)
    return {**stats(samples, sum(samples)), 'bytes_per_pass': size, 'sha256': expected.hexdigest(),
            'mib_per_second': round(size * len(samples) / (1024**2 * sum(samples)), 3),
            'path': 'legacy_manifest_import._collect_artifacts'}


def main():
    report = metadata('python -B -m perf.state_bench')
    with tempfile.TemporaryDirectory() as tmp, Resources(os.getpid()) as resources:
        root = Path(tmp).resolve()
        with Database.open(f'sqlite+pysqlite:///{(root / "state.db").as_posix()}') as db:
            db.create_all()
            report['sqlite_pragmas'] = sqlite_pragmas(db)
            report['store'] = store_bench(db)
            report['lease'] = contention_recovery(db)
        report['queue'] = run_queue(32, 1, False, 'single_mock')
        report['hash'] = hash_bench(root)
    report['resources'] = resources.report()
    report['ok'] = report['queue']['completed'] == report['queue']['jobs'] and report['queue']['active_leases'] == 0
    return 0 if publish('phase_k_state', report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
