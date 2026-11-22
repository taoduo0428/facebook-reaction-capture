"""Run the k6 API profile against the fake-manager server and record real numbers.

What it does:

1. picks a free port and starts ``perf/fake_server.py`` as a child process,
2. waits for ``/health``,
3. samples the child's CPU and RSS while k6 runs,
4. runs k6 with ``--summary-export`` and parses the summary,
5. writes ``perf/results/api_load_<timestamp>.json`` and prints a table.

Nothing here is simulated: the numbers come from the k6 run against a real
Uvicorn process.  The manager behind it is in-memory (no device), which the
report states explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "perf" / "results"
DEFAULT_K6_CANDIDATES = (
    Path(os.environ.get("TEMP", "")) / "k6" / "k6-v0.54.0-windows-amd64" / "k6.exe",
)


def find_k6() -> Path | None:
    override = os.environ.get("K6_BIN")
    if override and Path(override).is_file():
        return Path(override)
    found = shutil.which("k6")
    if found:
        return Path(found)
    for candidate in DEFAULT_K6_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise RuntimeError("fake server did not become ready")


class Sampler(threading.Thread):
    def __init__(self, pid: int, interval: float = 0.25):
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.samples: list[tuple[float, float | None, int | None]] = []
        self._stop = threading.Event()

    def run(self) -> None:
        from perf import sampler

        while not self._stop.is_set():
            reading = sampler.sample(self.pid)
            self.samples.append((time.monotonic(), reading.cpu_seconds, reading.rss_bytes))
            self._stop.wait(self.interval)

    def stop(self) -> dict[str, float | int | None]:
        self._stop.set()
        self.join(timeout=5)
        cpus = [cpu for _t, cpu, _r in self.samples if cpu is not None]
        rss = [value for _t, _c, value in self.samples if value is not None]
        cpu_delta = (max(cpus) - min(cpus)) if len(cpus) >= 2 else None
        return {
            "cpu_seconds": round(cpu_delta, 3) if cpu_delta is not None else None,
            "rss_peak_bytes": max(rss) if rss else None,
            "rss_mean_bytes": int(sum(rss) / len(rss)) if rss else None,
            "samples": len(self.samples),
        }


def percentile(metrics: dict, name: str, key: str) -> float | None:
    entry = metrics.get(name) or {}
    value = entry.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def run(k6: Path, base_url: str, script: Path, env: dict[str, str], export: Path) -> int:
    command = [str(k6), "run", "--quiet", "--summary-export", str(export), str(script)]
    merged = dict(os.environ)
    merged.update(env)
    merged["BASE_URL"] = base_url
    return subprocess.call(command, cwd=str(ROOT), env=merged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="k6 API load test with CPU/RSS sampling")
    parser.add_argument("--read-vus", default="8")
    parser.add_argument("--read-duration", default="10s")
    parser.add_argument("--submit-rate", default="20")
    parser.add_argument("--submit-duration", default="10s")
    parser.add_argument("--label", default="api")
    parser.add_argument("--results-dir", default=None, help="default: perf/results")
    args = parser.parse_args(argv)

    k6 = find_k6()
    if k6 is None:
        print(
            "k6 not found. Install it (https://k6.io/docs/get-started/installation/) "
            "or set K6_BIN=/path/to/k6.",
            file=sys.stderr,
        )
        return 2

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "perf" / "fake_server.py"), "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sampler = None
    try:
        wait_ready(base_url)
        from perf import sampler as process_sampler

        listener_pid = process_sampler.resolve_listener_pid(port)
        sampled_pid = listener_pid or server.pid
        sampler = Sampler(sampled_pid)
        sampler.start()
        results = Path(args.results_dir) if args.results_dir else RESULTS
        results.mkdir(parents=True, exist_ok=True)
        export = results / f".summary_{args.label}.json"
        started = datetime.now(timezone.utc)
        exit_code = run(
            k6,
            base_url,
            ROOT / "perf" / "k6_api.mjs",
            {
                "READ_VUS": args.read_vus,
                "READ_DURATION": args.read_duration,
                "SUBMIT_RATE": args.submit_rate,
                "SUBMIT_DURATION": args.submit_duration,
            },
            export,
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        resources = sampler.stop() if sampler else {}
        summary = json.loads(export.read_text(encoding="utf-8")) if export.is_file() else {}
        metrics = summary.get("metrics", {})
        http_reqs = metrics.get("http_reqs", {})
        report = {
            "label": args.label,
            "generated_at": started.isoformat().replace("+00:00", "Z"),
            "base_url": base_url,
            "k6": str(k6),
            "k6_version": subprocess.run(
                [str(k6), "version"], capture_output=True, text=True
            ).stdout.strip(),
            "profile": {
                "read_vus": args.read_vus,
                "read_duration": args.read_duration,
                "submit_rate": args.submit_rate,
                "submit_duration": args.submit_duration,
            },
            "wall_seconds": round(elapsed, 2),
            "exit_code": exit_code,
            "requests": {
                "count": int(http_reqs.get("count", 0)),
                "rps": round(float(http_reqs.get("rate", 0.0)), 2),
                "failed_rate": float(metrics.get("http_req_failed", {}).get("rate", 0.0)),
            },
            "latency_ms": {
                "p50": percentile(metrics, "http_req_duration", "p(50)"),
                "p90": percentile(metrics, "http_req_duration", "p(90)"),
                "p95": percentile(metrics, "http_req_duration", "p(95)"),
                "p99": percentile(metrics, "http_req_duration", "p(99)"),
                "avg": percentile(metrics, "http_req_duration", "avg"),
                "max": percentile(metrics, "http_req_duration", "max"),
            },
            "read_latency_ms": {
                "p50": percentile(metrics, "read_latency", "p(50)"),
                "p95": percentile(metrics, "read_latency", "p(95)"),
            },
            "submit_latency_ms": {
                "p50": percentile(metrics, "submit_latency", "p(50)"),
                "p95": percentile(metrics, "submit_latency", "p(95)"),
            },
            "server_resources": {
                "pid": sampled_pid,
                "pid_source": "listener" if listener_pid else "popen-child",
                **resources,
            },
        }
        stamp = started.strftime("%Y%m%d-%H%M%S")
        target = results / f"{args.label}_{stamp}.json"
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        export.unlink(missing_ok=True)
        print(json.dumps(report, indent=2))
        print(f"\nwritten: {target}")
        return exit_code
    finally:
        if sampler is not None:
            try:
                sampler.stop()
            except Exception:  # noqa: BLE001
                pass
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            server.kill()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
