"""Phase G. In-process HTTP requests, temporary SQLite, and a synthetic executor."""
from __future__ import annotations

import asyncio
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
from facebook_backend.executors.base import ExecutionOutcome, ExecutorPort
from facebook_backend.main import create_app
from facebook_backend.observability import context, tracing
from facebook_backend.observability.logging import configure_logging, get_logger, log_event
from facebook_backend.observability.metrics import METRICS
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.job_store import DatabaseJobStore
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.repositories import EventRepository, JobRepository
from facebook_backend.scheduler.device_registry import default_worker

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"
PARENT = f"00-{TRACE}-{SPAN}-01"
OTHER = "1" * 32
CANARY = "synthetic-private-value"


class TelemetryContractTests(unittest.TestCase):
    def setUp(self):
        # Keep application logger/tracer/registry changes local to the fixture.
        self.buffer = io.StringIO()
        logger = get_logger()
        self.enterContext(mock.patch.object(logger, "handlers", []))
        self.enterContext(mock.patch.object(logger, "propagate", False))
        self.addCleanup(logger.setLevel, logger.level)
        configure_logging(stream=self.buffer, force=True)
        METRICS.reset()
        self.addCleanup(METRICS.reset)
        self.addCleanup(tracing.reset_tracing)

    def exporter(self):
        if not tracing.sdk_available():
            self.skipTest("opentelemetry-sdk is not installed")
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = tracing.configure_tracing(exporter=exporter)
        self.addCleanup(provider.shutdown)
        return exporter

    def logs(self):
        return [json.loads(line) for line in self.buffer.getvalue().splitlines()]

    def client(self, manager):
        return TestClient(create_app(manager=manager, settings=Settings(load_local_config=False)),
                          base_url="http://127.0.0.1:8765")

    def test_unmatched_route_and_method_share_bounded_labels_and_keep_wire_contract(self):
        exporter = self.exporter()
        with self.client(object()) as client:
            for i in range(12):
                for path in (f"/{CANARY}-{i}", f"/v1/jobs/{CANARY}-{i}"):
                    response = client.request(f"PROBE{i}", path + f"?token={CANARY}")
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.content, b'{"error":"not found"}')
                    self.assertEqual(response.headers["content-length"], str(len(response.content)))
            client.get("/" + CANARY)
            metrics_text = client.get("/metrics").text
        self.assertEqual(METRICS.api_requests.value(method="OTHER", route="unmatched", status="404"), 24)
        self.assertEqual(METRICS.api_latency.snapshot(method="OTHER", route="unmatched")["count"], 24)
        self.assertEqual(METRICS.api_requests.value(method="GET", route="unmatched", status="404"), 1)
        output = metrics_text + self.buffer.getvalue() + str([s.to_json() for s in exporter.get_finished_spans()])
        self.assertNotIn(CANARY, output)
        self.assertNotIn("PROBE", output)

    def test_nested_errors_headers_and_urls_do_not_reach_json_text_or_sdk(self):
        exporter = self.exporter()
        fields = {"nested": [{"something": CANARY}], "error": CANARY, "Idempotency-Key": CANARY,
                  "submission_key": CANARY, "url": CANARY, "description": "https://fixture.invalid/" + CANARY,
                  "opaque": RuntimeError(CANARY), "Cookie": CANARY, "attempt_id": 1}
        with self.assertRaisesRegex(RuntimeError, CANARY):
            with context.bound(credentials=CANARY), tracing.span("fixture.failure", **fields):
                log_event(get_logger(), "fixture.failure", **fields)
                try:
                    raise RuntimeError(CANARY)
                except RuntimeError:
                    get_logger().exception("fixture.failure", stack_info=True)
                    raise
        self.assertEqual(context.current(), {})
        log = self.logs()
        self.assertEqual(log[0]["attempt_id"], 1)
        self.assertTrue(log[1]["exception"])
        self.assertNotIn(CANARY, self.buffer.getvalue())
        METRICS.registry.counter('fixture_total', 'fixture').inc(**fields)
        self.assertNotIn(CANARY, METRICS.render())
        error, = exporter.get_finished_spans()
        self.assertEqual(error.status.status_code.name, "ERROR")
        self.assertNotIn(CANARY, error.to_json())
        self.assertEqual(tuple(error.events), ())
        configure_logging(stream=self.buffer, json_format=False, force=True)
        try:
            raise ValueError(CANARY)
        except ValueError:
            get_logger().exception("fixture: %s", CANARY)
        self.assertNotIn(CANARY, self.buffer.getvalue())

    def test_contexts_are_isolated_across_await_and_invalid_duplicate_parents(self):
        exporter = self.exporter()
        ready = asyncio.Event()
        seen = []
        async def run(tid):
            with tracing.execution_context(f"00-{tid}-{SPAN}-01", job_id="fb_" + tid[:1]):
                seen.append(tid)
                if len(seen) == 2:
                    ready.set()
                await ready.wait()
                self.assertEqual(context.current()["trace_id"], tid)
                self.assertEqual(tracing.current_traceparent().split("-")[1], tid)
            self.assertEqual(context.current(), {})
        async def gather():
            await asyncio.gather(run(TRACE), run(OTHER))
        asyncio.run(gather())
        with self.client(object()) as client:
            for headers in ([('traceparent', PARENT), ('traceparent', PARENT)],
                            [('traceparent', "00-" + "0" * 32 + f"-{SPAN}-01")],
                            [('traceparent', CANARY)]):
                self.assertEqual(client.get('/unmatched', headers=headers).status_code, 404)
        http = [s for s in exporter.get_finished_spans() if s.name == 'http.request']
        self.assertEqual(len(http), 3)
        self.assertTrue(all(s.parent is None for s in http))
        self.assertEqual(len({s.context.trace_id for s in http}), 3)

    @unittest.skipUnless(tracing.sdk_available(), "opentelemetry-sdk is not installed")
    def test_default_http_entry_correlates_without_configuring_an_exporter(self):
        with self.client(object()) as client:
            response = client.get('/missing', headers={'traceparent': PARENT})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(next(r for r in self.logs() if r['event'] == 'http.request')['trace_id'], TRACE)
        self.assertEqual(tracing.trace_id_from_traceparent(PARENT), TRACE)
        for value in (None, CANARY, PARENT.replace(SPAN, '0' * 16), PARENT.upper(), PARENT + '\n'):
            self.assertIsNone(tracing.trace_id_from_traceparent(value))

    def test_without_sdk_keeps_response_and_metrics_and_clears_context(self):
        with mock.patch.object(tracing, "sdk_available", return_value=False):
            with tracing.execution_context(PARENT, job_id="fb_noop") as active:
                self.assertFalse(active.is_recording())
                self.assertIsNone(tracing.current_traceparent())
            with self.client(object()) as client:
                response = client.get('/missing', headers={"Traceparent": PARENT})
        self.assertEqual(response.content, b'{"error":"not found"}')
        self.assertEqual(METRICS.api_requests.value(method="GET", route="unmatched", status="404"), 1)
        self.assertEqual(context.current(), {})
        self.assertNotIn('trace_id', next(r for r in self.logs() if r['event'] == 'http.request'))

    def test_http_to_sql_to_worker_preserves_first_parent_and_counts_a_replay_once(self):
        exporter = self.exporter()
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(api, 'BASE_DIR', root))
        self.enterContext(mock.patch.object(api, 'adb_snapshot', autospec=True, return_value={}))
        self.enterContext(mock.patch.object(api, 'run_capture_job', side_effect=AssertionError('unexpected capture')))
        db = Database.open('sqlite+pysqlite:///' + (root / 'jobs.db').as_posix())
        self.addCleanup(db.dispose)
        Base.metadata.create_all(db.engine)
        executor = mock.create_autospec(ExecutorPort, instance=True)
        worker_context = {}
        entered, release = threading.Event(), threading.Event()
        def run(assignment, *, cancel_event):
            worker_context.update(context.snapshot())
            log_event(get_logger(), 'fixture.executed')
            entered.set()
            if not release.wait(10):
                raise RuntimeError('test executor timed out')
            return ExecutionOutcome(assignment.job_id, 'completed', result={'fixture': 1},
                                    process_state='stopped', transport_cleanup_state='restored')
        executor.execute.side_effect = run
        manager = api.JobManager(workers=[default_worker('fixture-device')], store=DatabaseJobStore(db),
                                 production_executor=executor)
        self.addCleanup(manager.shutdown, 5)
        self.addCleanup(release.set)
        with self.client(manager) as client:
            key = {"Idempotency-Key": CANARY, "baggage": "token=" + CANARY}
            payload = {"post_url": "https://www.facebook.com/fixture/posts/1"}
            first = client.post('/v1/jobs', json=payload, headers={**key, 'traceparent': PARENT})
            self.assertEqual(first.status_code, 202, first.content)
            job_id = first.json()['job_id']
            self.assertNotIn('traceparent', first.json())
            self.assertNotIn('request_id', first.json())
            self.assertTrue(entered.wait(10))
            replay = client.post('/v1/jobs', json=payload, headers={**key, 'traceparent': f'00-{OTHER}-{SPAN}-01'})
            self.assertEqual((replay.status_code, replay.json()['job_id']), (202, job_id))
            release.set()
            manager.shutdown(5)
        with db.session() as session:
            job = JobRepository(session).require(job_id)
            events = EventRepository(session).list_for_job(job_id)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(executor.execute.call_count, 1)
        self.assertEqual((METRICS.jobs_submitted.value(), METRICS.jobs_created.value()), (2, 1))
        original = {s.name: s for s in exporter.get_finished_spans() if s.context.trace_id == int(TRACE, 16)}
        self.assertEqual(original['http.request'].parent.span_id, int(SPAN, 16))
        self.assertEqual(original['job.create'].parent.span_id, original['http.request'].context.span_id)
        self.assertEqual(original['job.execute'].parent.span_id, original['job.create'].context.span_id)
        self.assertEqual(job['payload']['traceparent'], f"00-{TRACE}-{original['job.create'].context.span_id:016x}-01")
        self.assertEqual(worker_context['trace_id'], TRACE)
        self.assertEqual(worker_context['request_id'], job['payload']['request_id'])
        self.assertEqual(worker_context['job_id'], job_id)
        expected = {'job.created', 'job.queued', 'job.claimed', 'job.started', 'job.process_stopped',
                    'job.cleanup_completed', 'job.completed'}
        self.assertTrue(expected.issubset({e['event_type'] for e in events}))
        self.assertTrue(all(e['trace_id'] == TRACE for e in events))
        records = {r['event'] for r in self.logs() if r.get('trace_id') == TRACE}
        self.assertTrue({'job.submitted', 'http.request', 'fixture.executed'}.issubset(records))
        output = self.buffer.getvalue() + METRICS.render() + str(events) + str([s.to_json() for s in exporter.get_finished_spans()])
        self.assertNotIn(CANARY, output)


if __name__ == "__main__":
    unittest.main()
