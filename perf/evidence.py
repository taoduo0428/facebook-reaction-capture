"""Shared evidence for bounded fixture runs. No production configuration is loaded."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from perf.sampler import sample

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, timeout=10).decode().strip()

    files = list((ROOT / 'facebook_backend').rglob('*.py')) + list((ROOT / 'perf').glob('*.py'))
    files += [ROOT / 'perf/k6_api.mjs', ROOT / 'facebook_capture_api.py', ROOT / 'facebook_local.py']
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return {'commit': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
            'dirty': bool(git('status', '--porcelain')), 'source_files': len(files), 'source_sha256': digest.hexdigest()}


def metadata(command):
    return {'utc_start': datetime.now(timezone.utc).isoformat(), 'command': command, 'start_tree': snapshot(),
            'machine': {'platform': platform.platform(), 'arch': platform.machine(), 'logical_cpus': os.cpu_count(),
                        'processor': platform.processor(), 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                        **{p: importlib.metadata.version(p) for p in ('fastapi', 'uvicorn', 'sqlalchemy')}}}


def publish(label, report):
    report['end_tree'] = snapshot()
    report['same_source'] = report['start_tree'] == report['end_tree']
    report['utc_end'] = datetime.now(timezone.utc).isoformat()
    results = ROOT / 'perf/results'
    results.mkdir(exist_ok=True)
    target = results / (label + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with target.open('x', encoding='utf-8') as out:
        json.dump(report, out, indent=2, allow_nan=False)
        out.write('\n')
    print(target.relative_to(ROOT))
    if not report['same_source']:
        print('Source changed during measurement; see start_tree and end_tree.')
    return report['ok']


def quantiles_ms(seconds):
    values = sorted(seconds)
    return {f'p{p}': round(1000 * values[math.ceil(len(values) * p / 100) - 1], 3) if values else None
            for p in (50, 95, 99)}


class Resources:
    """Sample the specified process only. Delta covers the measurement window."""
    def __init__(self, pid):
        self.pid = pid
        self.readings = []
        self.stop = threading.Event()

    def __enter__(self):
        self.readings.append(sample(self.pid))
        def poll():
            while not self.stop.wait(0.1):
                self.readings.append(sample(self.pid))
        self.thread = threading.Thread(target=poll, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.readings.append(sample(self.pid))

    def report(self):
        start, end = self.readings[0].cpu_seconds, self.readings[-1].cpu_seconds
        rss = [r.rss_bytes for r in self.readings if r.rss_bytes is not None]
        return {'cpu_seconds': round(end - start, 5) if start is not None and end is not None else None,
                'sampled_peak_rss_bytes': max(rss, default=None), 'samples': len(self.readings)}
