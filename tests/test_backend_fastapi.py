"""FastAPI backend tests.

Two goals:

1. **Contract parity** — every legacy route keeps its status code, JSON body,
   headers and error semantics.  ``ContractParityTests`` runs the legacy
   ``CaptureServer`` and the new FastAPI app side by side against the same fake
   manager and compares raw responses byte for byte.
2. **Lifecycle correctness** — one manager per process, created in lifespan,
   never at import time, never per request; blocking work never on the loop.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

import facebook_capture_api as api
from facebook_backend.config import Settings
from facebook_backend.domain.statuses import BATCH_STATUSES, JOB_STATUSES
from facebook_backend.main import create_app
from facebook_backend.schemas.batch import BatchProgress
from facebook_backend.schemas.job import JobPublic
from facebook_backend.schemas.worker import HealthPayload

BASE_URL = "http://127.0.0.1:8765"

JOB = {
    "job_id": "fb_test",
    "status": "queued",
    "created_at": "2026-01-01T00:00:00+00:00",
    "started_at": None,
    "finished_at": None,
    "cancel_requested_at": None,
    "request": {
        "post_url": "https://www.facebook.com/example/posts/1",
        "post_id": "",
        "collect": ["reactions"],
        "save_artifacts": ["safe_capture", "progress"],
        "reaction_scroll_limit": None,
        "reaction_scroll_limit_explicit": False,
    },
    "completion_scope": None,
    "error": None,
    "run_dir": "runs/fb_test",
    "capture_run_dir": None,
    "attempt_dirs": [],
    "batch_id": None,
    "batch_index": None,
    "worker_id": None,
    "serial": None,
    "attempts": 0,
    "failover_count": 0,
    "process_pid": None,
    "process_creation_time": None,
    "process_state": None,
    "transport_cleanup_state": None,
    "partial_evidence_available": False,
}

BATCH = {
    "batch_id": "batch_test",
    "status": "running",
    "created_at": "2026-01-01T00:00:00+00:00",
    "total_posts": 1,
    "completed_posts": 0,
    "failed_posts": 0,
    "cancelled_posts": 0,
    "completion_scope": None,
    "posts": [{"job_id": "fb_test", "status": "running"}],
}

HEALTH = {
    "service": "ready",
    "mode": "primary-standby-serial",
    "scheduler": {"state": "running", "alive": True},
    "worker": "idle",
    "cancelling_jobs": 0,
    "queued_jobs": 0,
    "android": {"state": "device", "boot_completed": True, "proxy": ""},
    "serial": "emulator-5554",
    "active_worker_id": "worker-01",
    "last_failover": None,
    "capture_lease": {"state": "available", "job_id": None, "reason": None},
    "workers": [
        {
            "worker_id": "worker-01",
            "enabled": True,
            "serial": "emulator-5554",
            "avd_name": "fb-x86_64-api35",
            "capture_port": 8081,
            "native_port": 18766,
            "current_job_id": None,
            "cooldown_until": 0.0,
            "android": {"state": "device", "boot_completed": True, "proxy": ""},
        }
    ],
}


class FakeManager:
    """Deterministic manager used by both servers in the parity tests."""

    def __init__(self, *, artifact_root: Path | None = None):
        self.artifact_root = Path(artifact_root) if artifact_root else None
        self.calls: list[tuple[str, tuple]] = []
        self.lock = threading.Lock()

    def _record(self, name: str, *args):
        with self.lock:
            self.calls.append((name, args))

    # --- jobs ---------------------------------------------------------
    def health(self):
        self._record("health")
        return dict(HEALTH)

    def submit(self, payload):
        self._record("submit", payload)
        return dict(JOB, status="queued")

    def latest_job(self):
        self._record("latest_job")
        return dict(JOB)

    def public_job(self, job_id):
        self._record("public_job", job_id)
        if job_id == "missing":
            raise KeyError(job_id)
        if job_id == "conflict":
            raise api.ApiError("job is cancelling")
        return dict(JOB, job_id=job_id)

    def progress(self, job_id):
        self._record("progress", job_id)
        return {"job_status": "running", "storage_root": "runs/fb_test", "metrics": {}, "stages": [], "artifacts": []}

    def result(self, job_id):
        self._record("result", job_id)
        if job_id == "notdone":
            raise api.ApiError("job is running")
        return {"run_dir": "runs/fb_test", "result_outcome": "complete", "completeness": {"complete": True}}

    def artifact(self, job_id, artifact_name):
        self._record("artifact", job_id, artifact_name)
        if job_id == "missing":
            raise KeyError(job_id)
        if artifact_name == "unknown":
            raise api.ApiError("unknown artifact")
        return self._artifact_path(artifact_name)

    def cancel(self, job_id):
        self._record("cancel", job_id)
        if job_id == "missing":
            raise KeyError(job_id)
        return {"cancelled": True, "reason": "queued job cancelled", "job": dict(JOB, job_id=job_id, status="cancelled")}

    def reconcile_result_persistence(self, job_id, *, acknowledge=False):
        self._record("reconcile", job_id, acknowledge)
        if job_id == "missing":
            raise KeyError(job_id)
        return {"job_id": job_id, "reconciled": True, "acknowledged": acknowledge}

    # --- batches ------------------------------------------------------
    def submit_batch(self, payload):
        self._record("submit_batch", payload)
        return dict(BATCH)

    def latest_batch(self):
        self._record("latest_batch")
        return dict(BATCH)

    def batch_progress(self, batch_id):
        self._record("batch_progress", batch_id)
        if batch_id == "missing":
            raise KeyError(batch_id)
        return dict(BATCH, batch_id=batch_id)

    def batch_artifact(self, batch_id, artifact_name):
        self._record("batch_artifact", batch_id, artifact_name)
        if batch_id == "missing":
            raise KeyError(batch_id)
        if artifact_name == "unknown":
            raise api.ApiError("unknown artifact")
        return self._artifact_path(artifact_name)

    def cancel_batch(self, batch_id):
        self._record("cancel_batch", batch_id)
        if batch_id == "missing":
            raise KeyError(batch_id)
        return {"cancelled_jobs": 1, "batch": dict(BATCH, batch_id=batch_id)}

    # --- workers ------------------------------------------------------
    def reload_workers(self):
        self._record("reload_workers")
        return {"added": ["worker-03"], "updated": [], "worker_ids": ["worker-01", "worker-03"]}

    def set_worker_enabled(self, worker_id, enabled):
        self._record("set_worker_enabled", worker_id, enabled)
        if worker_id == "missing":
            raise KeyError(worker_id)
        if worker_id == "broken":
            raise api.SchedulerStartError("capture scheduler startup failed")
        return {"worker_id": worker_id, "enabled": enabled}

    def _artifact_path(self, artifact_name: str) -> Path:
        assert self.artifact_root is not None, "artifact root not configured"
        if artifact_name == "facebook-reactor-users.csv":
            return self.artifact_root / "facebook-reactor-users.csv"
        if artifact_name == "batch-summary.json":
            return self.artifact_root / "batch-summary.json"
        return self.artifact_root / artifact_name


class BackendTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "facebook-reactor-users.csv").write_text("user_id,name\n1,张三\n", encoding="utf-8")
        (self.root / "batch-summary.json").write_text('{"batch_id":"batch_test"}', encoding="utf-8")
        self.manager = FakeManager(artifact_root=self.root)
        self.settings = Settings(host="127.0.0.1", port=8765, load_local_config=False)
        self.app = create_app(manager=self.manager, settings=self.settings)
        self.client = TestClient(self.app, base_url=BASE_URL)
        self.client.__enter__()
        self.addCleanup(self._close_client)

    def _close_client(self):
        try:
            self.client.__exit__(None, None, None)
        except Exception:  # pragma: no cover - defensive cleanup
            pass

    def post_json(self, path, payload, **kwargs):
        return self.client.post(path, content=json.dumps(payload), headers={"content-type": "application/json"}, **kwargs)


class RouteInventoryTests(unittest.TestCase):
    """The migrated route table must cover every legacy endpoint."""

    EXPECTED = {
        ("GET", "/health"),
        ("GET", "/guide"),
        ("GET", "/v1/setup"),
        ("GET", "/v1/capabilities"),
        ("GET", "/v1/jobs/latest"),
        ("GET", "/v1/jobs/{job_id}"),
        ("GET", "/v1/jobs/{job_id}/progress"),
        ("GET", "/v1/jobs/{job_id}/result"),
        ("GET", "/v1/jobs/{job_id}/artifacts/{artifact_name}"),
        ("GET", "/v1/batches/latest"),
        ("GET", "/v1/batches/{batch_id}"),
        ("GET", "/v1/batches/{batch_id}/progress"),
        ("GET", "/v1/batches/{batch_id}/artifacts/{artifact_name}"),
        ("GET", "/v1/avatars/{user_id}"),
        ("POST", "/v1/jobs"),
        ("POST", "/v1/batches"),
        ("POST", "/v1/jobs/{job_id}/cancel"),
        ("POST", "/v1/batches/{batch_id}/cancel"),
        ("POST", "/v1/workers/reload"),
        ("POST", "/v1/workers/{worker_id}/enable"),
        ("POST", "/v1/workers/{worker_id}/disable"),
        ("POST", "/v1/jobs/{job_id}/reconcile-result-persistence"),
    }

    def test_every_legacy_route_is_registered(self):
        app = create_app(manager=FakeManager())
        registered = set()
        for route in app.routes:
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", "")
            for method in methods or ():
                if method in {"GET", "POST"}:
                    registered.add((method, path))
        missing = self.EXPECTED - registered
        self.assertFalse(missing, f"legacy routes missing from FastAPI app: {sorted(missing)}")

    def test_static_whitelist_matches_legacy(self):
        app = create_app(manager=FakeManager())
        paths = {route.path for route in app.routes if getattr(route, "path", None) in api.STATIC_ASSETS}
        self.assertEqual(paths, set(api.STATIC_ASSETS))

    def test_status_vocabulary_matches_legacy_source(self):
        source = Path(api.__file__).read_text(encoding="utf-8")
        for status in JOB_STATUSES:
            self.assertIn(f'"{status}"', source, f"job status {status!r} vanished from the legacy manager")
        for status in BATCH_STATUSES:
            self.assertIn(f'"{status}"', source, f"batch status {status!r} vanished from the legacy manager")

    def test_openapi_documents_submission_bodies(self):
        spec = create_app(manager=FakeManager()).openapi()

        def body(path):
            return spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]

        self.assertIn("post_url", body("/v1/jobs")["properties"])
        self.assertIn("reaction_scroll_limit", body("/v1/jobs")["properties"])
        self.assertIn("post_urls", body("/v1/batches")["properties"])
        ack = body("/v1/jobs/{job_id}/reconcile-result-persistence")
        self.assertEqual(ack["required"], ["acknowledge"])
        self.assertIs(ack["properties"]["acknowledge"]["const"], True)
        # /guide returns raw text and is intentionally absent from the schema.
        self.assertNotIn("/guide", spec["paths"])


class LifespanTests(unittest.TestCase):
    def test_manager_is_created_once_during_startup_and_never_per_request(self):
        created = []
        manager = FakeManager()

        def factory():
            created.append(1)
            return manager

        app = create_app(
            manager_factory=factory,
            settings=Settings(load_local_config=False),
        )
        self.assertIsNone(app.state.manager, "manager must not exist before startup")
        with TestClient(app, base_url=BASE_URL) as client:
            self.assertIs(app.state.manager, manager)
            for _ in range(5):
                self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(created, [1], "lifespan must build exactly one manager")

    def test_injected_manager_is_not_shut_down_by_the_app(self):
        class Manager(FakeManager):
            def __init__(self):
                super().__init__()
                self.shutdown_calls = 0

            def shutdown(self):
                self.shutdown_calls += 1

        manager = Manager()
        app = create_app(manager=manager, settings=Settings(load_local_config=False))
        with TestClient(app, base_url=BASE_URL):
            pass
        self.assertEqual(manager.shutdown_calls, 0, "the embedding caller owns an injected manager")

    def test_owned_manager_shutdown_hook_runs_once(self):
        class Manager(FakeManager):
            def __init__(self):
                super().__init__()
                self.shutdown_calls = 0

            def shutdown(self):
                self.shutdown_calls += 1

        manager = Manager()
        app = create_app(
            manager_factory=lambda: manager,
            settings=Settings(load_local_config=False),
        )
        with TestClient(app, base_url=BASE_URL):
            pass
        self.assertEqual(manager.shutdown_calls, 1)

    def test_importing_the_package_does_not_build_a_manager(self):
        import facebook_backend

        self.assertFalse(hasattr(facebook_backend, "JobManager"))
        app = facebook_backend.create_app(settings=Settings(load_local_config=False))
        self.assertIsNone(app.state.manager)


class TrustBoundaryTests(BackendTestCase):
    def test_non_loopback_host_is_rejected(self):
        client = TestClient(self.app, base_url="http://testserver")
        response = client.get("/health")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "untrusted Host"})

    def test_host_port_must_match_the_listening_port(self):
        response = self.client.get("/health", headers={"host": "127.0.0.1:9999"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "untrusted Host"})

    def test_non_loopback_hostname_is_rejected(self):
        response = self.client.get("/health", headers={"host": "example.com:8765"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "untrusted Host"})

    def test_cross_origin_request_is_rejected(self):
        response = self.client.get("/health", headers={"origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "cross-origin requests are not allowed"})

    def test_sec_fetch_site_cross_site_is_rejected(self):
        response = self.client.get("/health", headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "cross-site requests are not allowed"})

    def test_transfer_encoding_is_rejected_before_any_state_change(self):
        def body():
            yield b'{"post_url":"https://www.facebook.com/example/posts/1"}'

        response = self.client.post(
            "/v1/jobs",
            content=body(),
            headers={"transfer-encoding": "chunked"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "transfer encoding is not supported"})
        self.assertEqual(self.manager.calls, [], "no manager call may happen for a rejected request")

    def test_oversized_body_is_rejected(self):
        response = self.client.post(
            "/v1/jobs",
            content=b"x" * (64 * 1024 + 1),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "request body exceeds 65536 bytes"})


class JsonFormatTests(BackendTestCase):
    def test_json_body_is_compact_utf8_with_nosniff_headers(self):
        response = self.client.get("/v1/jobs/fb_test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn(b" ", response.content, "legacy bodies are serialised without spaces")
        self.assertNotIn(b"\\u", response.content, "non-ASCII must not be escaped")

    def test_unknown_path_returns_legacy_404_document(self):
        response = self.client.get("/nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not found"})

    def test_wrong_method_maps_to_404_like_the_legacy_router(self):
        response = self.client.get("/v1/jobs")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not found"})

    def test_trailing_slash_is_normalised_like_the_legacy_router(self):
        # Legacy `_segments()` dropped empty path parts, so "/health/" and
        # "/health" were the same route.  The adapter must not 404/307 instead.
        for path in ("/health/", "/health//"):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["service"], "ready")


class JobRouteTests(BackendTestCase):
    def test_health_returns_the_manager_payload(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"], "ready")
        HealthPayload.model_validate(payload)

    def test_submit_job_returns_202_and_forwards_the_payload(self):
        body = {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]}
        response = self.post_json("/v1/jobs", body)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "fb_test")
        JobPublic.model_validate(response.json())
        self.assertEqual(self.manager.calls[-1], ("submit", (body,)))

    def test_submit_job_rejects_empty_body(self):
        response = self.client.post("/v1/jobs", content=b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid request body size"})

    def test_submit_job_reports_json_parser_errors_like_the_legacy_server(self):
        response = self.client.post("/v1/jobs", content=b"{", headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Expecting property name", response.json()["error"])

    def test_submit_job_maps_normalisation_errors_to_400(self):
        with mock.patch.object(self.manager, "submit", side_effect=api.ApiError("post_url or post_id is required")):
            response = self.post_json("/v1/jobs", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "post_url or post_id is required"})

    def test_submit_job_maps_storage_failure_to_503(self):
        with mock.patch.object(self.manager, "submit", side_effect=OSError("disk")):
            response = self.post_json("/v1/jobs", {"post_url": "x"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("capture submission failed", response.json()["error"])

    def test_latest_job_404_when_no_jobs_exist(self):
        with mock.patch.object(self.manager, "latest_job", side_effect=KeyError("latest")):
            response = self.client.get("/v1/jobs/latest")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "no jobs found"})

    def test_job_not_found(self):
        response = self.client.get("/v1/jobs/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "job not found"})

    def test_job_state_conflict_is_409(self):
        response = self.client.get("/v1/jobs/conflict")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "job is cancelling"})

    def test_progress_and_result_routes(self):
        self.assertEqual(self.client.get("/v1/jobs/fb_test/progress").json()["job_status"], "running")
        result = self.client.get("/v1/jobs/fb_test/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["result_outcome"], "complete")

    def test_result_conflict_when_job_is_not_completed(self):
        response = self.client.get("/v1/jobs/notdone/result")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "job is running"})

    def test_cancel_returns_manager_payload(self):
        response = self.client.post("/v1/jobs/fb_test/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancelled"])
        self.assertEqual(self.manager.calls[-1], ("cancel", ("fb_test",)))

    def test_cancel_unknown_job(self):
        response = self.client.post("/v1/jobs/missing/cancel")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "job not found"})


class ArtifactRouteTests(BackendTestCase):
    def test_job_artifact_is_inline_for_csv(self):
        response = self.client.get("/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertEqual(
            response.headers["content-disposition"],
            'inline; filename="facebook-reactor-users.csv"',
        )
        self.assertEqual(response.headers["content-length"], str(len(response.content)))
        self.assertIn("张三", response.text)

    def test_download_flag_switches_to_attachment(self):
        response = self.client.get("/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv?download=1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))

    def test_extra_query_parameters_do_not_trigger_download(self):
        response = self.client.get(
            "/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv?download=1&x=2"
        )
        self.assertTrue(response.headers["content-disposition"].startswith("inline;"))

    def test_unknown_artifact_is_404(self):
        response = self.client.get("/v1/jobs/fb_test/artifacts/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "unknown artifact"})

    def test_artifact_for_missing_job_is_404(self):
        response = self.client.get("/v1/jobs/missing/artifacts/facebook-reactor-users.csv")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "job not found"})

    def test_missing_artifact_file_is_404_not_500(self):
        with mock.patch.object(
            self.manager,
            "artifact",
            return_value=self.root / "does-not-exist.csv",
        ):
            response = self.client.get("/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "artifact not found"})


class BatchRouteTests(BackendTestCase):
    def test_submit_batch_returns_202(self):
        response = self.post_json(
            "/v1/batches", {"post_urls": ["https://www.facebook.com/example/posts/1"]}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["batch_id"], "batch_test")
        BatchProgress.model_validate(response.json())

    def test_latest_batch_404_when_empty(self):
        with mock.patch.object(self.manager, "latest_batch", side_effect=KeyError("latest")):
            response = self.client.get("/v1/batches/latest")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "no batches found"})

    def test_batch_progress_on_both_paths(self):
        for path in ("/v1/batches/batch_test", "/v1/batches/batch_test/progress"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["batch_id"], "batch_test")

    def test_batch_not_found(self):
        response = self.client.get("/v1/batches/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "batch not found"})

    def test_batch_artifact(self):
        response = self.client.get("/v1/batches/batch_test/artifacts/batch-summary.json")
        self.assertEqual(response.status_code, 200)
        # Legacy only appended a charset to CSV artifacts.
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json(), {"batch_id": "batch_test"})

    def test_cancel_batch(self):
        response = self.client.post("/v1/batches/batch_test/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cancelled_jobs"], 1)

    def test_cancel_missing_batch(self):
        response = self.client.post("/v1/batches/missing/cancel")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "batch not found"})


class WorkerRouteTests(BackendTestCase):
    def test_reload_workers(self):
        response = self.client.post("/v1/workers/reload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"], ["worker-03"])

    def test_reload_conflict(self):
        with mock.patch.object(self.manager, "reload_workers", side_effect=api.ApiError("reload in progress")):
            response = self.client.post("/v1/workers/reload")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "reload in progress"})

    def test_reload_scheduler_start_failure_is_503(self):
        with mock.patch.object(
            self.manager,
            "reload_workers",
            side_effect=api.SchedulerStartError("capture scheduler startup failed"),
        ):
            response = self.client.post("/v1/workers/reload")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "worker reload could not start the scheduler; inspect state before retrying"},
        )

    def test_reload_io_failure_is_503(self):
        with mock.patch.object(self.manager, "reload_workers", side_effect=OSError("disk")):
            response = self.client.post("/v1/workers/reload")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "worker reload failed due to a local I/O error"})

    def test_enable_and_disable(self):
        self.assertTrue(self.client.post("/v1/workers/worker-01/enable").json()["enabled"])
        self.assertFalse(self.client.post("/v1/workers/worker-01/disable").json()["enabled"])

    def test_unknown_worker(self):
        response = self.client.post("/v1/workers/missing/enable")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "worker not found"})

    def test_worker_update_scheduler_failure_is_503(self):
        response = self.client.post("/v1/workers/broken/disable")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "worker update could not start the scheduler; inspect state before retrying"},
        )


class ReconcileRouteTests(BackendTestCase):
    PATH = "/v1/jobs/fb_test/reconcile-result-persistence"

    def test_requires_a_body(self):
        response = self.client.post(self.PATH)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid explicit acknowledgment body"})

    def test_requires_json_content_type(self):
        response = self.client.post(self.PATH, content=b'{"acknowledge":true}')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid explicit acknowledgment body"})

    def test_rejects_extra_fields(self):
        response = self.post_json(self.PATH, {"acknowledge": True, "force": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid explicit acknowledgment body"})

    def test_rejects_duplicate_keys(self):
        response = self.client.post(
            self.PATH,
            content=b'{"acknowledge":true,"acknowledge":true}',
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid explicit acknowledgment body"})

    def test_rejects_non_boolean_acknowledgment(self):
        response = self.post_json(self.PATH, {"acknowledge": "true"})
        self.assertEqual(response.status_code, 400)

    def test_accepts_the_exact_acknowledgment(self):
        response = self.post_json(self.PATH, {"acknowledge": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["acknowledged"], True)
        self.assertEqual(self.manager.calls[-1], ("reconcile", ("fb_test", True)))

    def test_content_type_with_charset_is_accepted(self):
        response = self.client.post(
            self.PATH,
            content=b'{"acknowledge":true}',
            headers={"content-type": "application/json; charset=utf-8"},
        )
        self.assertEqual(response.status_code, 200)

    def test_missing_job(self):
        response = self.post_json("/v1/jobs/missing/reconcile-result-persistence", {"acknowledge": True})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "job not found"})

    def test_conflict_is_409(self):
        with mock.patch.object(
            self.manager,
            "reconcile_result_persistence",
            side_effect=api.ApiError("result persistence is not blocked"),
        ):
            response = self.post_json(self.PATH, {"acknowledge": True})
        self.assertEqual(response.status_code, 409)


class SetupRouteTests(BackendTestCase):
    def test_capabilities(self):
        payload = self.client.get("/v1/capabilities").json()
        self.assertIn("platform", payload)
        self.assertIn("capture_pcap_supported", payload)

    def test_setup_reports_doctor_output(self):
        with mock.patch("facebook_local.doctor", return_value={"ok": True, "checks": []}):
            response = self.client.get("/v1/setup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "checks": []})

    def test_setup_failure_is_400(self):
        with mock.patch("facebook_local.doctor", side_effect=ValueError("bad config")):
            response = self.client.get("/v1/setup")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "bad config"})

    def test_guide_serves_readme(self):
        response = self.client.get("/guide")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn(b"Facebook", response.content)


class StaticAssetTests(BackendTestCase):
    def test_ui_assets_are_served_with_the_legacy_csp(self):
        for path, expected in (
            ("/", "text/html"),
            ("/index.html", "text/html"),
            ("/styles.css", "text/css"),
            ("/app.js", "javascript"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.headers["content-type"])
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn("default-src 'self'", response.headers["content-security-policy"])
                self.assertGreater(len(response.content), 20)


class AvatarRouteTests(BackendTestCase):
    def test_redirects_to_resolved_avatar(self):
        with mock.patch.object(api, "resolve_avatar_url", return_value="https://cdn.example/a.jpg"):
            response = self.client.get("/v1/avatars/10001", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://cdn.example/a.jpg")
        self.assertEqual(response.headers["cache-control"], "private, max-age=300")

    def test_unavailable_avatar_is_503(self):
        with mock.patch.object(api, "resolve_avatar_url", side_effect=api.ApiError("avatar lookup timed out")):
            response = self.client.get("/v1/avatars/10001")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "avatar lookup timed out"})


class MissingManagerTests(unittest.TestCase):
    def test_requests_before_lifespan_report_503(self):
        app = create_app(settings=Settings(load_local_config=False))

        async def call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                return await client.get("/health")

        response = asyncio.run(call())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "job manager is not initialized"})


SEEDED_MANIFEST = {
    "version": 1,
    "job_id": "fb_seeded",
    "status": "completed",
    "created_at": "2026-01-01T00:00:00Z",
    "started_at": "2026-01-01T00:00:01Z",
    "finished_at": "2026-01-01T00:00:02Z",
    "request": {
        "post_url": "https://www.facebook.com/example/posts/1",
        "post_id": "",
        "collect": ["reactions"],
        "save_artifacts": ["safe_capture", "progress"],
        "reaction_scroll_limit": None,
    },
    "completion_scope": "terminal",
    "error": None,
    "run_dir": "runs/fb_seeded",
    "capture_run_dir": "runs/fb_seeded",
    "attempt_dirs": ["runs/fb_seeded"],
    "worker_id": "worker-01",
    "serial": "emulator-5554",
    "process_state": "stopped",
}


class RealManagerIntegrationTests(unittest.TestCase):
    """Boot the FastAPI app over a **real** JobManager in an isolated root.

    The live ``api-jobs/`` tree is production-ish state and ``_load_persisted_jobs``
    rewrites manifests, so this test never points at it: ``BASE_DIR`` is patched
    to a temporary project root.
    """

    def test_real_job_manager_serves_health_and_manifest_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("api-jobs", "api-batches", "runs"):
                (root / name).mkdir()
            (root / "runs" / "fb_seeded").mkdir()
            (root / "api-jobs" / "fb_seeded.job.json").write_text(
                json.dumps(SEEDED_MANIFEST), encoding="utf-8"
            )
            with mock.patch.object(api, "BASE_DIR", root):
                app = create_app(
                    manager_factory=lambda: api.JobManager("emulator-5554"),
                    settings=Settings(base_dir=root, load_local_config=False),
                )
                with TestClient(app, base_url=BASE_URL) as client:
                    health = client.get("/health")
                    self.assertEqual(health.status_code, 200, health.text)
                    payload = health.json()
                    self.assertEqual(payload["service"], "ready")
                    self.assertEqual(payload["scheduler"]["state"], "running")
                    self.assertTrue(payload["scheduler"]["alive"])
                    HealthPayload.model_validate(payload)

                    latest = client.get("/v1/jobs/latest")
                    self.assertEqual(latest.status_code, 200, latest.text)
                    self.assertEqual(latest.json()["job_id"], "fb_seeded")
                    self.assertEqual(latest.json()["status"], "completed")
                    JobPublic.model_validate(latest.json())

                    missing = client.get("/v1/jobs/fb_unknown")
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.json(), {"error": "job not found"})

                    # The manifest recovery path must not have mutated the
                    # terminal record it read.
                    self.assertEqual(
                        json.loads((root / "api-jobs" / "fb_seeded.job.json").read_text("utf-8"))["status"],
                        "completed",
                    )


class EntrypointWiringTests(unittest.TestCase):
    ROOT = Path(api.__file__).resolve().parent

    def test_windows_launcher_defaults_to_fastapi_with_legacy_fallback(self):
        source = (self.ROOT / "start_facebook_capture_api.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-m facebook_backend", source)
        self.assertIn("[switch]$Legacy", source)
        self.assertIn("facebook_capture_api.py", source)

    def test_stop_script_recognises_both_entrypoints(self):
        source = (self.ROOT / "stop_all_facebook.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("'facebook_backend'", source)
        self.assertIn("'facebook_capture_api.py'", source)

    def test_entrypoint_refuses_multiple_workers(self):
        from facebook_backend import __main__ as entry

        with mock.patch.dict("os.environ", {"FACEBOOK_API_WORKERS": "2"}, clear=False):
            with self.assertRaises(SystemExit) as caught:
                entry.main([])
        self.assertIn("exactly one uvicorn worker", str(caught.exception))

    def test_entrypoint_refuses_non_loopback_bind(self):
        from facebook_backend import __main__ as entry

        with self.assertRaises(SystemExit) as caught:
            entry.main(["--host", "0.0.0.0"])
        self.assertIn("refusing non-loopback bind", str(caught.exception))


class ContractParityTests(unittest.TestCase):
    """Run both servers side by side and diff raw responses."""

    REQUESTS = [
        ("GET", "/health", None, None),
        ("GET", "/guide", None, None),
        ("GET", "/v1/capabilities", None, None),
        ("GET", "/v1/setup", None, None),
        ("GET", "/v1/jobs/latest", None, None),
        ("GET", "/v1/batches/latest", None, None),
        ("GET", "/v1/jobs/fb_test", None, None),
        ("GET", "/v1/jobs/missing", None, None),
        ("GET", "/v1/jobs/conflict", None, None),
        ("GET", "/v1/jobs/fb_test/progress", None, None),
        ("GET", "/v1/jobs/fb_test/result", None, None),
        ("GET", "/v1/jobs/notdone/result", None, None),
        ("GET", "/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv", None, None),
        ("GET", "/v1/jobs/fb_test/artifacts/facebook-reactor-users.csv?download=1", None, None),
        ("GET", "/v1/jobs/fb_test/artifacts/unknown", None, None),
        ("GET", "/v1/batches/batch_test", None, None),
        ("GET", "/v1/batches/batch_test/progress", None, None),
        ("GET", "/v1/batches/missing", None, None),
        ("GET", "/v1/batches/batch_test/artifacts/batch-summary.json", None, None),
        ("GET", "/v1/avatars/10001", None, None),
        ("GET", "/", None, None),
        ("GET", "/styles.css", None, None),
        ("GET", "/app.js", None, None),
        ("GET", "/nope", None, None),
        ("GET", "/v1/jobs", None, None),
        ("GET", "/health/", None, None),
        ("POST", "/v1/jobs", {"post_url": "https://www.facebook.com/example/posts/1"}, None),
        ("POST", "/v1/jobs", b"", None),
        ("POST", "/v1/jobs", b"{", None),
        ("POST", "/v1/batches", {"post_urls": ["https://www.facebook.com/example/posts/1"]}, None),
        ("POST", "/v1/jobs/fb_test/cancel", None, None),
        ("POST", "/v1/jobs/missing/cancel", None, None),
        ("POST", "/v1/batches/batch_test/cancel", None, None),
        ("POST", "/v1/batches/missing/cancel", None, None),
        ("POST", "/v1/workers/reload", None, None),
        ("POST", "/v1/workers/worker-01/enable", None, None),
        ("POST", "/v1/workers/worker-01/disable", None, None),
        ("POST", "/v1/workers/missing/enable", None, None),
        ("POST", "/v1/jobs/fb_test/reconcile-result-persistence", {"acknowledge": True}, None),
        ("POST", "/v1/jobs/fb_test/reconcile-result-persistence", None, None),
        ("POST", "/nope", None, None),
        ("GET", "/health", None, {"origin": "http://evil.example"}),
        ("GET", "/health", None, {"sec-fetch-site": "cross-site"}),
    ]

    COMPARED_HEADERS = ("content-type", "cache-control", "x-content-type-options", "content-disposition", "content-security-policy")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        (root / "facebook-reactor-users.csv").write_text("user_id,name\n1,张三\n", encoding="utf-8")
        (root / "batch-summary.json").write_text('{"batch_id":"batch_test"}', encoding="utf-8")
        cls.manager = FakeManager(artifact_root=root)
        cls._patchers = [
            mock.patch.object(api, "resolve_avatar_url", return_value="https://cdn.example/a.jpg"),
            mock.patch("facebook_local.doctor", return_value={"ok": True}),
        ]
        for patcher in cls._patchers:
            patcher.start()
        cls.legacy = _LegacyServer(cls.manager)
        cls.legacy.__enter__()
        cls.modern = _AsgiServer(create_app(manager=cls.manager, settings=Settings(load_local_config=False)))
        cls.modern.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.modern.__exit__(None, None, None)
        cls.legacy.__exit__(None, None, None)
        for patcher in cls._patchers:
            patcher.stop()
        cls._tmp.cleanup()

    def _call(self, server, method, path, body, headers):
        payload = body
        request_headers = dict(headers or {})
        if isinstance(body, dict):
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("content-type", "application/json")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        elif isinstance(body, bytes):
            payload = body
            request_headers.setdefault("content-type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, data
        finally:
            connection.close()

    def test_legacy_and_fastapi_responses_are_identical(self):
        for method, path, body, headers in self.REQUESTS:
            with self.subTest(method=method, path=path, body=body, headers=headers):
                old_status, old_headers, old_body = self._call(self.legacy, method, path, body, headers)
                new_status, new_headers, new_body = self._call(self.modern, method, path, body, headers)
                self.assertEqual(new_status, old_status, f"status mismatch for {method} {path}")
                self.assertEqual(new_body, old_body, f"body mismatch for {method} {path}")
                for name in self.COMPARED_HEADERS:
                    self.assertEqual(
                        new_headers.get(name),
                        old_headers.get(name),
                        f"header {name} mismatch for {method} {path}",
                    )


class _LegacyServer:
    def __init__(self, manager):
        self.manager = manager
        self.server = api.CaptureServer(("127.0.0.1", 0), manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.port = self.server.server_address[1]
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _AsgiServer:
    def __init__(self, app):
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("uvicorn test server did not start")
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
