"""Bounded HTTP measurements against this process's freshly bound loopback fixture."""
from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import json
import multiprocessing
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from perf.evidence import Resources, metadata, publish, quantiles_ms
from perf.fake_server import serve

RATES = {'read': 9, 'submit': 3}
SECONDS = 8
PAYLOAD = b'{"post_url":"fixture:synthetic"}'


def request(fixture, path, payload=None):
    conn = http.client.HTTPConnection('127.0.0.1', fixture['port'], timeout=1)
    try:
        conn.request('POST' if payload else 'GET', path, payload,
                     {'X-Fixture-Key': fixture['key'], 'Content-Type': 'application/json'})
        r = conn.getresponse()
        if r.getheader('X-Fixture-Id') != fixture['id']:
            raise RuntimeError('fixture identity mismatch')
        return r.status, r.read()
    finally:
        conn.close()


@contextmanager
def owned_fixture():
    ctx = multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 0))
        fixture = {'port': sock.getsockname()[1], 'key': secrets.token_hex(16), 'id': secrets.token_hex(16)}
        stop = ctx.Event()
        proc = ctx.Process(target=serve, args=(sock, fixture['key'], fixture['id'], tmp, stop))
        proc.start()
        try:
            deadline = time.monotonic() + 12
            while proc.is_alive() and time.monotonic() < deadline:
                try:
                    status, body = request(fixture, '/__fixture')
                    info = json.loads(body)
                    seed = info['job_id']
                    if status != 200 or not re.fullmatch(r'fb_[0-9a-f]{16}', seed):
                        raise RuntimeError('fixture readiness check failed')
                    fixture['seed'] = seed
                    break
                except (OSError, http.client.HTTPException):
                    time.sleep(0.05)
            else:
                raise RuntimeError('fixture did not start')
            yield fixture, info['pid']
        finally:
            stop.set()
            proc.join(3)
            if proc.is_alive():
                proc.terminate()
                proc.join(3)
            if proc.is_alive():
                raise RuntimeError('fixture did not stop')
            proc.close()


def timed_request(fixture, name, index):
    paths = ('/health', '/v1/jobs/latest', '/metrics', '/v1/jobs/' + fixture['seed'] + '/progress')
    start = time.perf_counter()
    try:
        if name == 'read':
            status, _ = request(fixture, paths[index % len(paths)])
            passed = status == 200
        else:
            status, body = request(fixture, '/v1/jobs', PAYLOAD)
            passed = status == 202 and re.fullmatch(r'fb_[a-f0-9]{16}', json.loads(body)['job_id']) is not None
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        passed = False
    return time.perf_counter() - start, passed


def python_load(fixture):
    # Two independent single-slot streams. Never queue missed arrivals or burst to catch up.
    slots = sorted((i / rate, name, i) for name, rate in RATES.items() for i in range(SECONDS * rate))
    sent = {name: [] for name in RATES}
    dropped = dict.fromkeys(RATES, 0)
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for offset, name, index in slots:
            target = start + offset
            time.sleep(max(0, target - time.perf_counter()))
            if (sent[name] and not sent[name][-1].done()) or time.perf_counter() >= target + 1 / RATES[name]:
                dropped[name] += 1
                continue
            sent[name].append(pool.submit(timed_request, fixture, name, index))
        time.sleep(max(0, start + SECONDS - time.perf_counter()))
    elapsed = time.perf_counter() - start
    streams = {}
    for name, futures in sent.items():
        samples = [f.result() for f in futures]
        passed = sum(ok for _, ok in samples)
        streams[name] = {'scheduled': SECONDS * RATES[name], 'dropped_iterations': dropped[name],
                         'attempts': len(samples), 'checks_passed': passed, 'checks_failed': len(samples) - passed,
                         'failed_rate': (len(samples) - passed) / len(samples) if samples else None,
                         'rps': round(len(samples) / elapsed, 3), 'ms': quantiles_ms(t for t, _ in samples)}
    return {'wall_seconds': round(elapsed, 4), 'streams': streams,
            'ok': all(s['attempts'] == s['checks_passed'] == s['scheduled'] for s in streams.values())}


def k6_load(fixture, exe):
    template = (ROOT / 'perf/k6_api.mjs').read_text(encoding='utf-8')
    marker = 'null /* OWNED_FIXTURE */'
    if template.count(marker) != 1:
        raise ValueError('invalid fixture template')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / 'profile.mjs').write_text(template.replace(marker, json.dumps(fixture)), encoding='utf-8')
        (path / 'empty.json').write_text('{}', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','HOME','USERPROFILE','LOCALAPPDATA','APPDATA'}}
        env.update(K6_NO_USAGE_REPORT='true', K6_ADDRESS='', NO_PROXY='*')
        cmd = [exe, 'run', '--quiet', '--no-color', '--no-usage-report', '--config', 'empty.json', 'profile.mjs']
        proc = subprocess.run(cmd, cwd=tmp, env=env, timeout=25, capture_output=True)
        summary = path / 'summary.json'
        if not summary.exists():
            raise RuntimeError(f'k6 did not write current summary, exit {proc.returncode}')
        return summarize_k6(json.loads(summary.read_text(encoding='utf-8')), proc.returncode)


def summarize_k6(summary, code):
    # k6 handleSummary uses metric.values. No missing metric is silently interpreted as zero.
    metrics = summary['metrics']
    elapsed = summary['elapsed_ms'] / 1000
    if elapsed <= 0:
        raise ValueError('k6 summary has no measurement window')
    groups = {c['name']: c for c in summary['root_group']['checks']}
    streams = {}
    for name, rate in RATES.items():
        latency = metrics[name + '_latency']['values']
        count = latency['count']
        checks = groups[name]
        streams[name] = {'attempts': count, 'scheduled': rate * SECONDS, 'rps': round(count / elapsed, 3),
                         'checks_passed': checks['passes'], 'checks_failed': checks['fails'],
                         'failed_rate': checks['fails'] / count if count else None,
                         'ms': {f'p{q}': latency[f'p({q})'] for q in (50,95,99)}}
    drop = metrics.get('dropped_iterations', {}).get('values', {}).get('count')
    attempts = sum(s['attempts'] for s in streams.values())
    accounted = all(s['attempts'] == s['scheduled'] == s['checks_passed'] and s['checks_failed'] == 0
                    for s in streams.values())
    rate = metrics.get('http_req_failed', {}).get('values', {}).get('rate')
    overall_checks = metrics['checks']['values']
    return {'streams': streams, 'wall_seconds': elapsed, 'k6_exit_code': code,
            'dropped_iterations': drop, 'http_failed_rate': rate, 'raw_checks': metrics['checks']['values'],
            'raw_http_reqs': metrics['http_reqs']['values'], 'raw_http_req_failed': metrics.get('http_req_failed', {}).get('values'),
            'raw_dropped_iterations': metrics.get('dropped_iterations', {}).get('values'),
            'ok': (code == 0 and drop == 0 and rate == 0 and accounted and overall_checks['fails'] == 0
                   and attempts == overall_checks['passes'] == metrics['http_reqs']['values']['count'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', choices=('python', 'k6'), default='python')
    args = parser.parse_args(argv)
    exe = shutil.which(os.environ.get('K6_BIN') or 'k6') if args.driver == 'k6' else None
    if args.driver == 'k6' and not exe:
        parser.error('k6 is absent from PATH. Use --driver python for a local run.')
    report = metadata('python -B -m perf.run_api_load --driver ' + args.driver)
    with owned_fixture() as (fixture, pid):
        with Resources(pid) as resources:
            result = k6_load(fixture, exe) if exe else python_load(fixture)
    report.update(result, driver=args.driver, target='127.0.0.1', seconds=SECONDS, offered_rps=RATES,
                  max_clients=2, server_resources=resources.report())
    if exe:
        report['k6_version'] = subprocess.check_output([exe,'version'], text=True, timeout=5).strip()
    return 0 if publish('phase_k_http', report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
