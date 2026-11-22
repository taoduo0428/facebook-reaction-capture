"""Phase 7: the existing frontends and the Python client against the new backend.

Three layers, all automated:

1. **Static client→route contract** — every path ``web/app.js`` and
   ``web_v2/app.js`` call is matched against the FastAPI route table (method and
   normalised template).  Removing or renaming a route fails here instead of in
   a browser.
2. **Status vocabulary** — every job/batch status the backend can emit appears
   in the UI's label maps, so ``partial``/``failed``/``cancelled``/``cancelling``
   keep rendering.
3. **Real end-to-end client** — ``facebook_api_client.FacebookApiClient`` talks
   to a live Uvicorn instance of the FastAPI app (fake manager, no device):
   submit → poll → wait → result → download → cancel → error mapping.
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

import requests

import facebook_api_client as client_module
from facebook_backend.config import Settings
from facebook_backend.domain.statuses import BATCH_STATUSES, JOB_STATUSES
from facebook_backend.main import create_app

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8765"

JOB = {
    "job_id": "fb_compat",
    "status": "completed",
    "created_at": "2026-01-01T00:00:00Z",
    "started_at": "2026-01-01T00:00:01Z",
    "finished_at": "2026-01-01T00:00:02Z",
    "cancel_requested_at": None,
    "request": {"post_url": "https://www.facebook.com/x/posts/1"},
    "completion_scope": "terminal",
    "error": None,
    "run_dir": "runs/fb_compat",
    "attempt_dirs": ["runs/fb_compat"],
    "batch_id": None,
    "batch_index": None,
    "worker_id": "worker-00",
    "serial": "emulator-5554",
    "attempts": 1,
    "failover_count": 0,
    "process_state": "stopped",
    "transport_cleanup_state": "restored",
    "partial_evidence_available": False,
}

BATCH = {
    "batch_id": "batch_compat",
    "status": "completed",
    "created_at": "2026-01-01T00:00:00Z",
    "total_posts": 1,
    "completed_posts": 1,
    "failed_posts": 0,
    "cancelled_posts": 0,
    "posts": [{"job_id": "fb_compat", "status": "completed"}],
}


class FakeManager:
    """Deterministic manager covering every path the clients use."""

    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.accepting_submissions = True
        self.jobs: dict[str, dict[str, Any]] = {"fb_compat": dict(JOB)}
        self.cancelled: list[str] = []

    def health(self):
        return {
            "service": "ready",
            "worker": "idle",
            "android": {"state": "device", "boot_completed": True, "proxy": ""},
            "serial": "emulator-5554",
            "workers": [],
        }

    def submit(self, payload, *, submission_key=None):
        job_id = "fb_compat"
        self.jobs[job_id] = dict(JOB, status="queued", submission_key=submission_key)
        return dict(self.jobs[job_id])

    def public_job(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return dict(self.jobs[job_id])

    def latest_job(self):
        return dict(self.jobs["fb_compat"])

    def progress(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return {
            "job_status": self.jobs[job_id]["status"],
            "storage_root": "runs/fb_compat",
            "metrics": {},
            "stages": [],
            "artifacts": [
                {
                    "name": "facebook-reactor-users.csv",
                    "label": "Reaction 用户结果",
                    "layer": "解析结果",
                    "path": "runs/fb_compat/analysis/facebook-reactor-users.csv",
                    "size_bytes": 18,
                    "state": "ready",
                    "viewable": True,
                    "url": "/v1/jobs/{job_id}/artifacts/facebook-reactor-users.csv",
                    "required": True,
                    "selected": True,
                }
            ],
        }

    def result(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return {
            "run_dir": "runs/fb_compat",
            "result_outcome": "complete",
            "completion_scope": "terminal",
            "completeness": {"complete": True},
            "reaction_users": 3,
        }

    def artifact(self, job_id, artifact_name):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.artifact_root / "facebook-reactor-users.csv"

    def cancel(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        self.cancelled.append(job_id)
        self.jobs[job_id] = dict(self.jobs[job_id], status="cancelling")
        return {"cancelled": True, "reason": "running job cancellation requested", "job": dict(self.jobs[job_id])}

    def submit_batch(self, payload):
        return dict(BATCH)

    def latest_batch(self):
        return dict(BATCH)

    def batch_progress(self, batch_id):
        if batch_id != "batch_compat":
            raise KeyError(batch_id)
        return dict(BATCH)

    def batch_artifact(self, batch_id, artifact_name):
        return self.artifact_root / "batch-summary.json"

    def cancel_batch(self, batch_id):
        if batch_id != "batch_compat":
            raise KeyError(batch_id)
        return {"cancelled_jobs": 1, "batch": dict(BATCH, status="cancelled")}

    def reload_workers(self):
        return {"added": [], "updated": [], "worker_ids": ["worker-00"]}

    def set_worker_enabled(self, worker_id, enabled):
        return {"worker_id": worker_id, "enabled": enabled}

    def reconcile_result_persistence(self, job_id, *, acknowledge=False):
        return {"job_id": job_id, "reconciled": True}


def js_api_calls(source: str) -> list[tuple[str, str]]:
    """Extract ``(method, path)`` for every ``api(...)`` call in a JS file."""
    calls: list[tuple[str, str]] = []
    for match in re.finditer(r"api\(\s*(`[^`]+`|\"[^\"]+\"|'[^']+')", source):
        literal = match.group(1)[1:-1]
        tail = source[match.end() : match.end() + 160]
        method = "POST" if re.search(r"method:\s*[\"']POST[\"']", tail) else "GET"
        calls.append((method, literal))
    return calls


def normalize_path(path: str) -> str:
    """``/v1/jobs/${encodeURIComponent(id)}/progress`` → ``/v1/jobs/{}/progress``."""
    path = re.sub(r"\$\{[^}]+\}", "{}", path)
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path.split("?", 1)[0]


def route_table(app) -> set[tuple[str, str]]:
    table: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in methods or ():
            if method in {"GET", "POST"}:
                table.add((method, normalize_path(path)))
    return table


def matches_route(routes: set[tuple[str, str]], method: str, concrete_path: str) -> bool:
    """True when a concrete path (real ids/names) fits one of the route templates."""
    for route_method, template in routes:
        if route_method != method:
            continue
        pattern = "^" + re.escape(template).replace(r"\{\}", "[^/]+") + "$"
        if re.fullmatch(pattern, concrete_path):
            return True
    return False


class FrontendRouteContractTests(unittest.TestCase):
    """Every API path the UIs call must exist on the FastAPI app."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app(manager=FakeManager(Path(".")), settings=Settings(load_local_config=False))
        cls.routes = route_table(cls.app)

    def test_web_calls_only_existing_routes(self):
        calls = js_api_calls((ROOT / "web" / "app.js").read_text(encoding="utf-8"))
        self.assertTrue(calls, "web/app.js should call the API")
        missing = [
            (method, path)
            for method, path in calls
            if (method, normalize_path(path)) not in self.routes
        ]
        self.assertEqual(missing, [], f"web/app.js calls routes the backend does not serve: {missing}")

    def test_web_v2_calls_only_existing_routes(self):
        calls = js_api_calls((ROOT / "web_v2" / "app.js").read_text(encoding="utf-8"))
        self.assertTrue(calls, "web_v2/app.js should call the API")
        missing = [
            (method, path)
            for method, path in calls
            if (method, normalize_path(path)) not in self.routes
        ]
        self.assertEqual(missing, [], f"web_v2/app.js calls routes the backend does not serve: {missing}")

    def test_artifact_url_placeholder_resolves_to_a_route(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('replace("{job_id}"', source, "the UI substitutes {job_id} into artifact.url")
        # The placeholder the UI substitutes must be exactly what the backend emits.
        progress = FakeManager(Path(".")).progress("fb_compat")
        url = progress["artifacts"][0]["url"]
        self.assertIn("{job_id}", url)
        resolved = url.replace("{job_id}", "fb_compat")
        self.assertTrue(
            matches_route(self.routes, "GET", resolved),
            f"artifact url {resolved} does not fit any GET route",
        )

    def test_download_uses_the_exact_download_query(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("?download=1", source)
        # The backend's exact-match rule was verified in Phase 1; this pins the
        # UI to that literal so the two cannot drift apart.
        self.assertRegex(source, r"\$\{url\}\?download=1")


class StatusRenderingContractTests(unittest.TestCase):
    def test_every_backend_status_has_a_ui_label(self):
        tokens = set(re.findall(r'"([a-z_]+)"', (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8")))
        missing = (set(JOB_STATUSES) | set(BATCH_STATUSES)) - tokens
        self.assertEqual(missing, set(), f"web_v2/app.js does not handle: {sorted(missing)}")

    def test_web_legacy_ui_handles_the_terminal_states(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for status in ("cancelled", "failed", "completed", "cancelling"):
            with self.subTest(status=status):
                self.assertIn(status, source)

    def test_ui_surfaces_the_backend_error_message(self):
        for relative in ("web/app.js", "web_v2/app.js"):
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(source, r"\.error\s*\|\|", "the UI must show the API's error field")


class LiveServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        (root / "facebook-reactor-users.csv").write_text("user_id,name\n1,张三\n", encoding="utf-8")
        (root / "batch-summary.json").write_text('{"batch_id":"batch_compat"}', encoding="utf-8")
        cls.manager = FakeManager(root)
        app = create_app(manager=cls.manager, settings=Settings(load_local_config=False))
        cls.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 20
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cls.server.started:
            raise RuntimeError("uvicorn test server did not start")
        cls.port = cls.server.servers[0].sockets[0].getsockname()[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls._tmp.cleanup()


class ApiClientEndToEndTests(LiveServerCase):
    def client(self, **kwargs) -> client_module.FacebookApiClient:
        return client_module.FacebookApiClient(self.base_url, **kwargs)

    def test_submit_status_wait_result_download_cancel(self):
        with self.client() as api:
            submitted = api.submit("https://www.facebook.com/x/posts/1", collect=["reactions"])
            self.assertEqual(submitted["job_id"], "fb_compat")
            status = api.get_status("fb_compat")
            self.assertEqual(status["status"], "queued")
            # The fake reports completed after submit; wait returns the terminal payload.
            self.manager.jobs["fb_compat"] = dict(JOB, status="completed")
            terminal = api.wait("fb_compat", overall_timeout=5, poll_interval=0.05)
            self.assertEqual(terminal["status"], "completed")
            result = api.result("fb_compat")
            self.assertEqual(result["result_outcome"], "complete")
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "reactors.csv"
                path = api.download("fb_compat", "facebook-reactor-users.csv", target)
                self.assertTrue(path.is_file())
                self.assertIn("张三", path.read_text(encoding="utf-8"))
            cancelled = api.cancel("fb_compat")
            self.assertTrue(cancelled["cancelled"])
            self.assertEqual(self.manager.cancelled, ["fb_compat"])

    def test_wait_reports_failed_and_cancelled_as_job_errors(self):
        with self.client() as api:
            self.manager.jobs["fb_compat"] = dict(JOB, status="failed", error={"message": "boom"})
            with self.assertRaises(client_module.FacebookJobError) as caught:
                api.wait("fb_compat", overall_timeout=5, poll_interval=0.05)
            self.assertEqual(caught.exception.status, "failed")
            self.manager.jobs["fb_compat"] = dict(JOB, status="cancelled")
            with self.assertRaises(client_module.FacebookJobError) as caught:
                api.wait("fb_compat", overall_timeout=5, poll_interval=0.05)
            self.assertEqual(caught.exception.status, "cancelled")
            self.manager.jobs["fb_compat"] = dict(JOB)

    def test_unknown_job_maps_to_the_backend_error_message(self):
        with self.client() as api:
            with self.assertRaises(client_module.FacebookApiHttpError) as caught:
                api.get_status("fb_missing")
            self.assertEqual(caught.exception.status_code, 404)
            self.assertIn("job not found", str(caught.exception))

    def test_batch_flow_is_reachable_by_the_client_transport(self):
        with self.client() as api:
            response = api._request_json(
                "POST", "/v1/batches", payload={"post_urls": ["https://www.facebook.com/x/posts/1"]}
            )
            self.assertEqual(response["batch_id"], "batch_compat")
            progress = api._request_json("GET", "/v1/batches/batch_compat/progress")
            self.assertEqual(progress["status"], "completed")
            cancelled = api._request_json("POST", "/v1/batches/batch_compat/cancel")
            self.assertEqual(cancelled["cancelled_jobs"], 1)

    def test_artifact_download_honours_the_exact_download_query(self):
        # The UI appends ?download=1; the backend must treat it as an attachment.
        with self.client() as api:
            response = api._request(
                "GET", "/v1/jobs/fb_compat/artifacts/facebook-reactor-users.csv?download=1"
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])

    def test_health_and_capabilities_are_readable(self):
        with self.client() as api:
            health = api._request_json("GET", "/health")
            self.assertEqual(health["service"], "ready")
            capabilities = api._request_json("GET", "/v1/capabilities")
            self.assertIn("platform", capabilities)

    def test_client_is_requests_based_and_never_retries_a_post(self):
        # A guard for the client's documented contract: submission is one-shot.
        source = Path(client_module.__file__).read_text(encoding="utf-8")
        self.assertIn("import requests", source)
        self.assertNotIn("Retry(", source)
        self.assertNotIn("urllib3.util.retry", source)


class CancellationRenderingTests(unittest.TestCase):
    def test_cancelling_and_cancelled_have_distinct_labels(self):
        # Requirement: a cancellation *request* must not look like a stopped job.
        # The UI can only show that if the two states render differently.
        for relative in ("web/app.js", "web_v2/app.js"):
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(source, r"cancelling:\s*\"[^\"]+\"")
                self.assertRegex(source, r"cancelled:\s*\"[^\"]+\"")
                cancelling = re.search(r"cancelling:\s*\"([^\"]+)\"", source).group(1)
                cancelled = re.search(r"cancelled:\s*\"([^\"]+)\"", source).group(1)
                self.assertNotEqual(cancelling, cancelled)


class BatchConsistencyTests(unittest.TestCase):
    def test_batch_summary_counts_match_job_statuses(self):
        import facebook_capture_api as api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [
                {
                    "job_id": "fb_a",
                    "status": "completed",
                    "run_dir": str(root / "post-001"),
                    "batch_id": "batch_x",
                    "batch_index": 1,
                    "request": {"post_url": "https://www.facebook.com/x/posts/1"},
                    "error": None,
                    "completion_scope": "terminal",
                    "attempt_dirs": [],
                },
                {
                    "job_id": "fb_b",
                    "status": "failed",
                    "run_dir": str(root / "post-002"),
                    "batch_id": "batch_x",
                    "batch_index": 2,
                    "request": {"post_url": "https://www.facebook.com/x/posts/2"},
                    "error": "boom",
                    "completion_scope": None,
                    "attempt_dirs": [],
                },
            ]
            summary = api.write_batch_outputs(root, "batch_x", jobs)
            self.assertEqual(summary["status"], "completed_with_errors")
            self.assertEqual(summary["status_counts"]["completed"], 1)
            self.assertEqual(summary["status_counts"]["failed"], 1)
            self.assertEqual(summary["total_posts"], 2)
            # The UI downloads this exact file; it must agree with the API payload.
            written = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(written["status"], summary["status"])
            self.assertEqual(written["status_counts"], summary["status_counts"])

    def test_batch_status_is_one_of_the_documented_states(self):
        import facebook_capture_api as api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = [
                {
                    "job_id": "fb_a",
                    "status": "running",
                    "run_dir": str(root / "post-001"),
                    "batch_id": "batch_y",
                    "batch_index": 1,
                    "request": {"post_url": "https://www.facebook.com/x/posts/1"},
                    "error": None,
                    "completion_scope": None,
                    "attempt_dirs": [],
                }
            ]
            summary = api.write_batch_outputs(root, "batch_y", jobs)
            self.assertIn(summary["status"], BATCH_STATUSES)


class FrontendAssetTests(unittest.TestCase):
    def test_static_assets_are_still_served(self):
        from fastapi.testclient import TestClient

        app = create_app(manager=FakeManager(Path(".")), settings=Settings(load_local_config=False))
        client = TestClient(app, base_url=BASE_URL)
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None))
        for path in ("/", "/index.html", "/styles.css", "/app.js"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_web_v2_is_served_by_its_own_launcher_not_the_api(self):
        # web_v2 has its own server (start_ui_v2.py); the API must not claim to
        # serve it, otherwise the two UIs would silently diverge.
        app = create_app(manager=FakeManager(Path(".")), settings=Settings(load_local_config=False))
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertNotIn("/v2", paths)
        self.assertTrue((ROOT / "start_ui_v2.py").is_file())


if __name__ == "__main__":
    unittest.main()
