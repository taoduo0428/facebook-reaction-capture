"""Container control plane (Phase L).

The image carries no ADB/SDK/emulator, so it must not own execution.  These
tests pin that: reads answer, writes are refused with 503, the packaging stays
free of device tooling, and the live state directories are not mounted into a
container that cannot verify a capture process.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from facebook_backend.config import Settings
from facebook_backend.container import CONTROL_PLANE_MESSAGE, ControlPlaneManager, create_control_app
from facebook_backend.domain.exceptions import ServiceUnavailableError

ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.manager = ControlPlaneManager()

    def test_health_is_ready_but_declares_the_control_plane(self):
        health = self.manager.health()
        self.assertEqual(health["service"], "ready")
        self.assertEqual(health["mode"], "control-plane")
        self.assertEqual(health["scheduler"], {"state": "disabled", "alive": False})
        self.assertFalse(self.manager.accepting_submissions)

    def test_writes_are_refused_not_queued(self):
        for call in (
            lambda: self.manager.submit({"post_url": "https://www.facebook.com/1/posts/2"}),
            lambda: self.manager.submit_batch({"post_urls": ["https://www.facebook.com/1/posts/2"]}),
            lambda: self.manager.cancel("fb_1"),
            lambda: self.manager.reconcile_result_persistence("fb_1", acknowledge=True),
        ):
            with self.subTest(call=call), self.assertRaises(ServiceUnavailableError) as caught:
                call()
            self.assertIn("control plane", str(caught.exception))

    def test_unknown_reads_stay_not_found(self):
        with self.assertRaises(KeyError):
            self.manager.public_job("fb_missing")
        with self.assertRaises(KeyError):
            self.manager.latest_job()

    def test_http_health_and_refused_submission(self):
        app = create_control_app(settings=Settings(load_local_config=False))
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["mode"], "control-plane")
            refused = client.post("/v1/jobs", json={"post_url": "https://www.facebook.com/1/posts/2"})
            self.assertEqual(refused.status_code, 503)
            self.assertIn("control plane", refused.text)

    def test_image_has_no_device_tooling_and_uses_the_control_entrypoint(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8-sig")
        self.assertIn("facebook_backend.container", dockerfile)
        self.assertNotIn("facebook_backend.main:create_app", dockerfile)
        # Comments explain why the toolchain is absent; only instructions count.
        instructions = "\n".join(
            line for line in dockerfile.splitlines() if line.strip() and not line.strip().startswith("#")
        ).lower()
        for banned in ("adb ", "android-sdk", "emulator", "platform-tools"):
            self.assertNotIn(banned, instructions)

    def test_compose_does_not_mount_live_execution_state(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8-sig")
        for path in ("./runs", "./api-jobs", "./api-batches"):
            self.assertNotIn(path, compose)
        self.assertIn("./data", compose)
        self.assertIn("127.0.0.1:8765:8765", compose)

    def test_container_workflow_checks_the_real_image(self):
        workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
        for step in ("docker compose config", "docker compose build", "docker compose up", "/health", "docker compose down"):
            self.assertIn(step, workflow)


if __name__ == "__main__":
    unittest.main()
