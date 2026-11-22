"""Observability tests (Phase 6).

Proves the four claims the phase makes:

1. every log line is JSON and carries the job/batch/worker/device/attempt/trace
   fields, and never carries a credential,
2. a request produces one span with the context as attributes and a trace id
   that nested work can reuse,
3. the Prometheus text endpoint exposes real values (not decorative counters),
4. the API and the scheduler actually record them (latency, queue depth,
   execution time, retries, failovers, cancellations).
"""
from __future__ import annotations

import io
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import facebook_capture_api as api
from facebook_backend.config import Settings
from facebook_backend.executors.mock import MockExecutor, completed, failed
from facebook_backend.main import create_app
from facebook_backend.observability import context
from facebook_backend.observability.logging import JsonFormatter, configure_logging, redact
from facebook_backend.observability.metrics import METRICS, MetricsRegistry, PlatformMetrics
from facebook_backend.observability.tracing import (
    configure_tracing,
    current_trace_id,
    reset_tracing,
    sdk_available,
    span,
)
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import JobRepository
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.manager import Scheduler

BASE_URL = "http://127.0.0.1:8765"


class LogCapture:
    """Route the backend logger into a buffer and parse the JSON lines."""

    def __init__(self, level: str = "DEBUG"):
        self.buffer = io.StringIO()
        # force=True: an earlier test (or the app lifespan) may already have
        # installed a stderr handler; the capture must win deterministically.
        self.logger = configure_logging(level, stream=self.buffer, force=True)

    def records(self) -> list[dict]:
        self.logger.handlers[0].flush()
        return [json.loads(line) for line in self.buffer.getvalue().splitlines() if line.strip()]

    def close(self) -> None:
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self):
        self.capture = LogCapture()
        self.addCleanup(self.capture.close)

    def test_log_line_is_json_with_context_fields(self):
        from facebook_backend.observability.logging import get_logger, log_event

        logger = get_logger("test")
        with context.bound(job_id="fb_1", worker_id="worker-00", device_serial="emulator-5554"):
            log_event(logger, "job.completed", attempt_id="1", extra_field="x")
        record = self.capture.records()[-1]
        self.assertEqual(record["event"], "job.completed")
        self.assertEqual(record["job_id"], "fb_1")
        self.assertEqual(record["worker_id"], "worker-00")
        self.assertEqual(record["device_serial"], "emulator-5554")
        self.assertEqual(record["attempt_id"], "1")
        self.assertEqual(record["extra_field"], "x")
        self.assertEqual(record["level"], "INFO")
        self.assertTrue(record["ts"].endswith("Z"))

    def test_credentials_are_never_logged(self):
        self.assertTrue(redact({"api_key": "x", "password": "y", "job_id": "fb_1"}) == {"job_id": "fb_1"})
        from facebook_backend.observability.logging import get_logger, log_event

        log_event(
            get_logger("test"),
            "login",
            password="hunter2",
            authorization="Bearer abc",
            cookie="session=1",
            job_id="fb_1",
        )
        line = self.capture.buffer.getvalue()
        self.assertNotIn("hunter2", line)
        self.assertNotIn("Bearer abc", line)
        self.assertNotIn("session=1", line)
        self.assertIn("fb_1", line)

    def test_formatter_emits_one_object_per_line(self):
        formatter = JsonFormatter()
        record = logging.LogRecord("facebook_backend.x", logging.INFO, __file__, 1, "evt", None, None)
        record.fields = {"job_id": "fb_9"}
        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["event"], "evt")
        self.assertEqual(payload["job_id"], "fb_9")


class ContextTests(unittest.TestCase):
    def test_bound_merges_and_restores(self):
        with context.bound(job_id="fb_1"):
            self.assertEqual(context.current()["job_id"], "fb_1")
            with context.bound(worker_id="worker-00"):
                self.assertEqual(context.current(), {"job_id": "fb_1", "worker_id": "worker-00"})
            self.assertEqual(context.current(), {"job_id": "fb_1"})
        self.assertEqual(context.current(), {})

    def test_context_is_per_thread(self):
        seen: dict[str, dict] = {}

        def worker(name: str) -> None:
            with context.bound(job_id=name):
                seen[name] = context.current()

        threads = [threading.Thread(target=worker, args=(f"fb_{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(seen["fb_0"], {"job_id": "fb_0"})
        self.assertEqual(seen["fb_1"], {"job_id": "fb_1"})


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(reset_tracing)

    def test_noop_tracer_when_not_configured(self):
        self.assertIsNone(current_trace_id())
        with span("unit.noop", job_id="fb_1") as active:
            self.assertFalse(active.is_recording())

    @unittest.skipUnless(sdk_available(), "opentelemetry-sdk is not installed")
    def test_spans_carry_context_and_share_a_trace_id(self):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        configure_tracing(exporter=exporter)
        with context.bound(job_id="fb_1", worker_id="worker-00"):
            with span("job.execute", device_id="worker-00"):
                outer_trace = current_trace_id()
                with span("process.cleanup"):
                    inner_trace = current_trace_id()
        self.assertIsNotNone(outer_trace)
        self.assertEqual(len(outer_trace), 32)
        self.assertEqual(outer_trace, inner_trace)
        spans = {span_.name: span_ for span_ in exporter.get_finished_spans()}
        self.assertIn("job.execute", spans)
        attributes = dict(spans["job.execute"].attributes)
        self.assertEqual(attributes["job_id"], "fb_1")
        self.assertEqual(attributes["device_id"], "worker-00")
        self.assertEqual(attributes["trace_id"], outer_trace)


class MetricsTests(unittest.TestCase):
    def test_counter_gauge_histogram_math(self):
        registry = MetricsRegistry()
        counter = registry.counter("c_total", "help")
        counter.inc()
        counter.inc(2, label="a")
        self.assertEqual(counter.value(), 1.0)
        self.assertEqual(counter.value(label="a"), 2.0)
        gauge = registry.gauge("g", "help")
        gauge.set(3, state="busy")
        gauge.dec(1, state="busy")
        self.assertEqual(gauge.value(state="busy"), 2.0)
        histogram = registry.histogram("h", "help", buckets=[1, 2, 5, 10])
        for value in (0.5, 1.5, 3, 8, 20):
            histogram.observe(value)
        snapshot = histogram.snapshot()
        self.assertEqual(snapshot["count"], 5)
        self.assertEqual(snapshot["sum"], 33.0)
        self.assertEqual(snapshot["max"], 20.0)
        self.assertLessEqual(snapshot["p50"], snapshot["p95"])
        self.assertLessEqual(snapshot["p95"], snapshot["p99"])

    def test_prometheus_rendering(self):
        registry = MetricsRegistry()
        registry.counter("jobs_total", "help").inc(2, status="completed")
        histogram = registry.histogram("latency_seconds", "help", buckets=[1, 5])
        histogram.observe(0.5)
        registry.gauge("queue_depth", "help").set(4)
        text = registry.render()
        self.assertIn("# TYPE jobs_total counter", text)
        self.assertIn('jobs_total{status="completed"} 2', text)
        self.assertIn("# TYPE latency_seconds histogram", text)
        self.assertIn('latency_seconds_bucket{le="1"} 1', text)
        self.assertIn('latency_seconds_bucket{le="+Inf"} 1', text)
        self.assertIn("latency_seconds_sum 0.5", text)
        self.assertIn("latency_seconds_count 1", text)
        self.assertIn("# TYPE queue_depth gauge", text)
        self.assertIn("queue_depth 4", text)

    def test_platform_metrics_reset_rebuilds_the_set(self):
        metrics = PlatformMetrics()
        metrics.jobs_submitted.inc()
        metrics.reset()
        self.assertEqual(metrics.jobs_submitted.value(), 0.0)
        self.assertIn("facebook_jobs_submitted_total", metrics.render())


class ApiObservabilityTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()
        self.capture = LogCapture()
        self.addCleanup(self.capture.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        class Manager:
            accepting_submissions = True

            def health(self):
                return {"service": "ready"}

            def submit(self, payload, *, submission_key=None):
                return {"job_id": "fb_obs", "status": "queued", "batch_id": None}

            def public_job(self, job_id):
                return {"job_id": job_id, "status": "queued"}

        self.app = create_app(manager=Manager(), settings=Settings(load_local_config=False))
        self.client = TestClient(self.app, base_url=BASE_URL)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_request_is_measured_and_logged(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(METRICS.api_requests.value(method="GET", route="/health", status="200"), 1.0)
        self.assertEqual(METRICS.api_latency.snapshot(method="GET", route="/health")["count"], 1)
        record = [r for r in self.capture.records() if r["event"] == "http.request"][-1]
        self.assertEqual(record["http_route"], "/health")
        self.assertEqual(record["http_status"], 200)
        self.assertIn("duration_ms", record)
        self.assertTrue(record["request_id"])

    def test_route_label_uses_the_template_not_the_raw_id(self):
        self.client.get("/v1/jobs/fb_secret_id")
        labels = [
            (key, value)
            for key, value in METRICS.api_requests._values.items()
        ]
        rendered = str(labels)
        self.assertIn("/v1/jobs/{job_id}", rendered)
        self.assertNotIn("fb_secret_id", rendered)

    def test_job_creation_is_counted_and_logged(self):
        response = self.client.post(
            "/v1/jobs",
            content=json.dumps({"post_url": "https://www.facebook.com/x/posts/1"}),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(METRICS.jobs_submitted.value(), 1.0)
        record = [r for r in self.capture.records() if r["event"] == "job.submitted"][-1]
        self.assertEqual(record["job_id"], "fb_obs")

    def test_request_body_is_not_logged(self):
        self.client.post(
            "/v1/jobs",
            content=json.dumps({"post_url": "https://www.facebook.com/x/posts/1", "password": "s3cret"}),
            headers={"content-type": "application/json"},
        )
        self.assertNotIn("s3cret", self.capture.buffer.getvalue())

    def test_metrics_endpoint_serves_prometheus_text(self):
        self.client.get("/health")
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("version=0.0.4", response.headers["content-type"])
        self.assertIn("facebook_api_requests_total", response.text)
        self.assertIn('route="/health"', response.text)


class ArtifactMetricTests(unittest.TestCase):
    """The completion rule is the only place verification is counted."""

    def setUp(self):
        METRICS.reset()

    def test_verification_outcomes_are_counted(self):
        from facebook_backend.artifacts.manager import (
            ARTIFACT_PATHS,
            REQUIRED_ARTIFACTS,
            required_artifacts_ready,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(required_artifacts_ready(root))
            self.assertEqual(METRICS.artifact_verifications.value(result="incomplete"), 1.0)
            for name in REQUIRED_ARTIFACTS:
                path = root / ARTIFACT_PATHS[name]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("user_id\n1\n", encoding="utf-8")
            self.assertTrue(required_artifacts_ready(root))
        self.assertEqual(METRICS.artifact_verifications.value(result="ready"), 1.0)
        self.assertIn("facebook_artifact_verifications_total", METRICS.render())


class SchedulerObservabilityTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()
        self.capture = LogCapture()
        self.addCleanup(self.capture.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'o.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)
        self.clock_value = 1_700_000_000.0

    def now(self) -> float:
        return self.clock_value

    def enqueue(self, job_id: str, **fields) -> dict:
        values = {
            "status": "queued",
            "payload": {"request": {"post_url": "https://www.facebook.com/x/posts/1"}},
            "created_at": "2026-01-01T00:00:00Z",
        }
        values.update(fields)
        with self.database.session() as session:
            return JobRepository(session).create(job_id=job_id, **values)

    def scheduler(self, executor=None, devices: int = 1) -> Scheduler:
        workers = [
            WorkerConfig(f"worker-{i:02d}", f"emulator-{5554 + i * 2}", f"avd-{i:02d}", 8081 + i, 18766 + i, True)
            for i in range(devices)
        ]
        return Scheduler(
            database=self.database,
            devices=workers,
            executor=executor or MockExecutor(),
            clock=self.now,
        )

    def test_tick_records_queue_depth_and_completion(self):
        self.enqueue("fb_a")
        scheduler = self.scheduler()
        scheduler.tick()
        self.assertEqual(METRICS.scheduler_ticks.value(), 1.0)
        self.assertEqual(METRICS.queue_depth.value(), 0.0)
        self.assertEqual(METRICS.jobs_completed.value(status="completed"), 1.0)
        snapshot = METRICS.job_execution.snapshot(device="worker-00", status="completed")
        self.assertEqual(snapshot["count"], 1)
        record = [r for r in self.capture.records() if r["event"] == "job.completed"][-1]
        self.assertEqual(record["job_id"], "fb_a")
        self.assertEqual(record["device_id"], "worker-00")

    def test_device_gauges_reflect_state(self):
        from facebook_backend.scheduler.dispatcher import DispatchPlan
        from facebook_backend.scheduler.models import Device

        scheduler = self.scheduler(devices=3)
        devices = scheduler.devices(now=self.now())
        devices[0] = Device.from_worker_config(
            WorkerConfig("worker-00", "emulator-5554", "avd-0", 8081, 18766, True),
            current_job_id="fb_running",
        )
        devices[1] = Device.from_worker_config(
            WorkerConfig("worker-01", "emulator-5556", "avd-1", 8082, 18767, True),
            cooldown_until=self.now() + 60,
        )
        plan = DispatchPlan(assignments=(), blocked={"worker-02": "lease-held"})
        scheduler._publish_device_metrics(devices, plan, now=self.now())
        self.assertEqual(METRICS.devices.value(state="busy"), 1.0)
        self.assertEqual(METRICS.devices.value(state="cooldown"), 1.0)
        self.assertEqual(METRICS.devices.value(state="blocked"), 1.0)
        self.assertEqual(METRICS.devices.value(state="available"), 0.0)

    def test_concurrency_limit_is_not_reported_as_blocked(self):
        from facebook_backend.scheduler.dispatcher import DispatchPlan
        from facebook_backend.scheduler.models import Device

        scheduler = self.scheduler(devices=2)
        devices = scheduler.devices(now=self.now())
        plan = DispatchPlan(assignments=(), blocked={"worker-01": "concurrency-limit"})
        scheduler._publish_device_metrics(devices, plan, now=self.now())
        self.assertEqual(METRICS.devices.value(state="available"), 2.0)
        self.assertEqual(METRICS.devices.value(state="blocked"), 0.0)

    def test_rate_limit_records_a_retry_without_failover(self):
        self.enqueue("fb_a")
        scheduler = self.scheduler(
            executor=MockExecutor(script={"fb_a": failed("fb_a", FailureClass.RATE_LIMIT, "429", process_state="stopped", transport_cleanup_state="restored")}),
            devices=2,
        )
        scheduler.tick()
        self.assertEqual(METRICS.retries.value(failure="rate-limit"), 1.0)
        self.assertEqual(METRICS.failovers.value(), 0.0)
        record = [r for r in self.capture.records() if r["event"] == "job.retry_scheduled"][-1]
        self.assertIs(record["same_device"], True)
        self.assertEqual(record["failure"], "rate-limit")

    def test_device_failure_records_a_failover(self):
        self.enqueue("fb_a")
        scheduler = self.scheduler(
            executor=MockExecutor(script={"fb_a": failed("fb_a", FailureClass.DEVICE, "device offline", process_state="stopped", transport_cleanup_state="restored")}),
            devices=2,
        )
        scheduler.tick()
        self.assertEqual(METRICS.failovers.value(), 1.0)
        self.assertEqual(METRICS.retries.value(failure="device"), 1.0)

    def test_cancellation_outcomes_are_distinguished(self):
        self.enqueue("fb_a")
        scheduler = self.scheduler(
            executor=MockExecutor(
                script={"fb_a": failed("fb_a", FailureClass.EXECUTOR, "no stop", process_state=None)}
            )
        )
        started = threading.Event()

        class SlowExecutor:
            def execute(self, assignment, *, cancel_event=None):
                started.set()
                cancel_event.wait(timeout=5)
                return failed(assignment.job_id, FailureClass.EXECUTOR, "no stop")

        scheduler = self.scheduler(executor=SlowExecutor())
        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        started.wait(timeout=5)
        scheduler.request_cancel("fb_a")
        thread.join(timeout=10)
        self.assertEqual(METRICS.cancellations.value(outcome="unverified"), 1.0)
        self.assertEqual(METRICS.cancellations.value(outcome="verified-stop"), 0.0)

    def test_recovery_actions_are_counted(self):
        from facebook_backend.scheduler.recovery import RecoveryPlanner

        self.enqueue("fb_a", status="running", process_pid=4242, process_creation_time=1.0, transport_cleanup_state="restored")
        RecoveryPlanner(
            self.database, identity_probe=lambda pid, created: "absent", clock=self.now
        ).run()
        self.assertEqual(METRICS.recoveries.value(action="requeue"), 1.0)


if __name__ == "__main__":
    unittest.main()
