"""Performance harness tests (Phase 8).

The harness itself has to be trustworthy before its numbers are: these tests run
the scheduler benchmark for real (small), read the current process through the
CPU/RSS sampler, syntax-check the k6 profile with node, and make sure the report
cites the commands that produced its numbers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class K6ProfileTests(unittest.TestCase):
    def test_profile_is_valid_javascript(self):
        node = "node"
        result = subprocess.run(
            [node, "--check", str(ROOT / "perf" / "k6_api.mjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_covers_reads_and_submissions(self):
        source = (ROOT / "perf" / "k6_api.mjs").read_text(encoding="utf-8")
        for token in ("/health", "/v1/jobs/latest", "/metrics", "/v1/jobs", "/progress"):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertIn("thresholds", source)
        self.assertIn("summaryTrendStats", source)


class SamplerTests(unittest.TestCase):
    def test_samples_the_current_process(self):
        sys.path.insert(0, str(ROOT))
        from perf import sampler

        reading = sampler.sample(os.getpid())
        self.assertEqual(reading.pid, os.getpid())
        self.assertIsNotNone(reading.rss_bytes)
        self.assertGreater(reading.rss_bytes or 0, 1_000_000)
        self.assertIsNotNone(reading.cpu_seconds)
        self.assertGreaterEqual(reading.cpu_seconds or 0, 0)

    def test_unreadable_pid_reports_none_instead_of_a_guess(self):
        sys.path.insert(0, str(ROOT))
        from perf import sampler

        reading = sampler.sample(999_999_999)
        self.assertIsNone(reading.rss_bytes)
        self.assertIsNone(reading.cpu_seconds)

    def test_ps_cputime_parsing(self):
        sys.path.insert(0, str(ROOT))
        from perf import sampler

        self.assertAlmostEqual(sampler._parse_cputime("0:00.12"), 0.12)
        self.assertAlmostEqual(sampler._parse_cputime("12:34"), 754.0)
        self.assertAlmostEqual(sampler._parse_cputime("1:02:03"), 3723.0)
        self.assertIsNone(sampler._parse_cputime("not-a-time"))

    @unittest.skipIf(sys.platform == "win32", "the ps fallback is POSIX-only")
    def test_ps_fallback_reports_the_current_process_without_procfs(self):
        """macOS has no /proc; the sampler must still answer."""
        sys.path.insert(0, str(ROOT))
        from perf import sampler

        reading = sampler._ps_sample(os.getpid())
        self.assertEqual(reading.pid, os.getpid())
        self.assertIsNotNone(reading.rss_bytes)
        self.assertGreater(reading.rss_bytes or 0, 1_000_000)
        self.assertIsNotNone(reading.cpu_seconds)
        self.assertGreaterEqual(reading.cpu_seconds or 0, 0)


class SchedulerBenchmarkTests(unittest.TestCase):
    def test_benchmark_completes_every_job_and_records_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "perf" / "scheduler_bench.py"),
                    "--jobs",
                    "20",
                    "--devices",
                    "1",
                    "--label",
                    "harness_self_test",
                    "--results-dir",
                    tmp,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            files = list(Path(tmp).glob("harness_self_test_*.json"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(report["completed"], 20)
        self.assertGreater(report["throughput_jobs_per_second"], 0)
        self.assertGreaterEqual(report["queue_latency_ms"]["p95"], 0)
        self.assertEqual(report["per_device"], {"worker-00": 20})

    def test_parallel_benchmark_uses_both_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "perf" / "scheduler_bench.py"),
                    "--jobs",
                    "20",
                    "--devices",
                    "2",
                    "--parallel",
                    "--label",
                    "harness_parallel",
                    "--results-dir",
                    tmp,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(next(Path(tmp).glob("harness_parallel_*.json")).read_text("utf-8"))
        self.assertEqual(report["completed"], 20)
        self.assertEqual(set(report["per_device"]), {"worker-00", "worker-01"})


class FakeServerTests(unittest.TestCase):
    def test_build_app_serves_metrics_and_health(self):
        from fastapi.testclient import TestClient

        sys.path.insert(0, str(ROOT))
        from perf import fake_server

        temp = self.enterContext(tempfile.TemporaryDirectory())
        app = fake_server.build_app("test-key", "test-server", Path(temp), 8765)
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        self.assertEqual(client.get("/health").status_code, 403)
        client.headers["X-Fixture-Key"] = "test-key"
        self.assertEqual(client.get("/__fixture").headers["X-Fixture-Id"], "test-server")
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None))
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/metrics").status_code, 200)
        submitted = client.post(
            "/v1/jobs",
            content=json.dumps({"post_url": "https://www.facebook.com/x/posts/1"}),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(submitted.status_code, 202)
        job_id = submitted.json()["job_id"]
        self.assertEqual(client.get(f"/v1/jobs/{job_id}/progress").status_code, 200)


class PhaseKEvidenceTests(unittest.TestCase):
    def summary(self):
        checks = [{'name': 'read', 'passes': 72, 'fails': 0}, {'name': 'submit', 'passes': 24, 'fails': 0}]
        metrics = {name: {'values': value} for name, value in {
            'checks': {'rate': 1.0, 'passes': 96, 'fails': 0}, 'http_req_failed': {'rate': 0.0},
            'http_reqs': {'count': 96}, 'dropped_iterations': {'count': 0},
            'read_latency': {'count': 72, 'p(50)': 1, 'p(95)': 2, 'p(99)': 3},
            'submit_latency': {'count': 24, 'p(50)': 1, 'p(95)': 2, 'p(99)': 3},
        }.items()}
        return {'metrics': metrics, 'root_group': {'checks': checks}, 'elapsed_ms': 8000}

    def test_absent_rate_or_dropped_count_never_passes(self):
        from perf.run_api_load import summarize_k6
        self.assertTrue(summarize_k6(self.summary(), 0)['ok'])
        for metric, field in [('http_req_failed', 'http_failed_rate'), ('dropped_iterations', 'dropped_iterations')]:
            with self.subTest(metric=metric):
                summary = self.summary()
                del summary['metrics'][metric]
                report = summarize_k6(summary, 0)
                self.assertIsNone(report[field])
                self.assertFalse(report['ok'])

    def test_dropped_failed_and_unaccounted_each_fail_the_gate(self):
        from perf.run_api_load import summarize_k6
        for metric, key, value in [('checks', 'fails', 1), ('dropped_iterations', 'count', 1),
                                   ('http_req_failed', 'rate', 0.01), ('http_reqs', 'count', 95)]:
            with self.subTest(metric=metric):
                summary = self.summary()
                summary['metrics'][metric]['values'][key] = value
                self.assertFalse(summarize_k6(summary, 0)['ok'])

    def test_profile_requires_a_rendered_fixture_and_has_no_env_overrides(self):
        text = (ROOT / 'perf/k6_api.mjs').read_text(encoding='utf-8')
        self.assertNotIn('__ENV', text)
        self.assertIn('const fixture = null /* OWNED_FIXTURE */;', text)
        self.assertIn('http://127.0.0.1:', text)
        self.assertIn('maxRedirects: 0', text)
        self.assertIn('maxVUs: 1', text)

    def test_state_measurements_check_store_and_exclusive_recovery(self):
        from perf.state_bench import Database, contention_recovery, store_bench
        with tempfile.TemporaryDirectory() as tmp:
            with Database.open(f'sqlite+pysqlite:///{(Path(tmp) / "fixture.db").as_posix()}') as db:
                db.create_all()
                self.assertEqual(store_bench(db)['verified_rows_per_read'], 32)
                r = contention_recovery(db)
                self.assertEqual((r['acquired'], r['held']), (12, 12))
                self.assertEqual(r['recovery_cancel_release']['count'], 12)


class PerformanceReportTests(unittest.TestCase):
    def test_report_exists_and_cites_reproducible_commands(self):
        report = ROOT / "docs" / "backend-modernization" / "PERF_REPORT.md"
        self.assertTrue(report.is_file(), "Phase 8 must produce PERF_REPORT.md")
        text = report.read_text(encoding="utf-8")
        for token in (
            "run_api_load.py",
            "scheduler_bench.py",
            "k6_api.mjs",
            "k6",
            "p95",
            "MockExecutor",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        # The boundary must be stated in the report itself, not just in a commit.
        self.assertIn("无设备", text)
        self.assertIn("真实设备", text)

    def test_results_directory_is_not_tracked(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/perf/results/", ignore)


if __name__ == "__main__":
    unittest.main()
