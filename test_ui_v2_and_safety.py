#!/usr/bin/env python3
"""
Test suite to verify that:
1. NO existing user files were modified (cryptographic hash integrity check).
2. The new web_v2 UI and start_ui_v2 server function correctly end-to-end.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Baseline hashes recorded before any changes
BASELINE_HASHES = {
    "web/index.html": "89fe24d7e36144d35f76b3dc8657003259e5870f92916b49d3d15f9572008e17",
    "web/styles.css": "20c587ed11a49e77efa6eea8026d24f074f2869c14bbcc9ddfbc4674826812cd",
    "web/app.js": "109bc3b54089c4789074560512d31fecec6f17aaa2ebf73bbac1192e5b613231",
    "facebook_capture_api.py": "5267bb83ecfafdffee721a9dc9b1ba22237ba3124928ad817df84c5f7654963f",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest().lower()


class UserFilesSafetyCheckTests(unittest.TestCase):
    def test_existing_user_files_are_strictly_unmodified(self):
        """Verify that absolutely zero existing files were modified or corrupted."""
        for rel_path, expected_hash in BASELINE_HASHES.items():
            full_path = ROOT / rel_path
            self.assertTrue(full_path.exists(), f"File missing: {rel_path}")
            current_hash = sha256_file(full_path)
            self.assertEqual(
                current_hash,
                expected_hash,
                f"SAFETY VIOLATION: Existing user file was modified! {rel_path}"
            )


class WebV2IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start start_ui_v2.py on port 8769 for testing
        cls.test_port = 8769
        cls.server_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "start_ui_v2.py"), "--port", str(cls.test_port), "--no-browser"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait up to 5 seconds for server to be responsive
        url = f"http://127.0.0.1:{cls.test_port}/"
        ready = False
        for _ in range(20):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                pass
        if not ready:
            cls.server_proc.terminate()
            raise RuntimeError("Test UI v2 server failed to start within timeout.")

    @classmethod
    def tearDownClass(cls):
        if cls.server_proc:
            cls.server_proc.terminate()
            try:
                cls.server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.server_proc.kill()

    def test_serves_index_html(self):
        url = f"http://127.0.0.1:{self.test_port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertIn("text/html", content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("OmniCapture Console v2", body)
            self.assertIn("01 / BATCH INGEST", body)
            self.assertIn("02 / SWARM MATRIX", body)
            self.assertIn("03 / STAGE PIPELINE", body)
            self.assertIn("04 / LIVE TELEMETRY", body)
            self.assertIn("05 / ARTIFACTS VAULT", body)

    def test_serves_styles_css(self):
        url = f"http://127.0.0.1:{self.test_port}/styles.css"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertIn("text/css", content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("theme-dark", body)
            self.assertIn("kpi-ribbon", body)
            self.assertIn("stage-pulse", body)

    def test_serves_app_js(self):
        url = f"http://127.0.0.1:{self.test_port}/app.js"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertTrue("javascript" in content_type or "text/" in content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("OmniCapture Console v2", body)
            self.assertIn("drawNetworkGraph", body)
            self.assertIn("renderBatchMatrix", body)

    def test_proxies_health_or_returns_graceful_503(self):
        url = f"http://127.0.0.1:{self.test_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                self.assertIn(resp.status, (200, 503))
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, 503)
            body = err.read().decode("utf-8")
            self.assertIn("Backend API on port 8765 is not reachable", body)


class WebV2BatchSelectionTests(unittest.TestCase):
    def test_startup_restores_latest_batch_without_jumping_active_selection(self):
        source = (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8")
        self.assertIn('latest = await api("/v1/batches/latest")', source)
        self.assertIn("await restoreLatestBatch();", source)
        self.assertIn('const ACTIVE_BATCH_STATES = new Set(["running", "queued"]);', source)
        self.assertIn('if (ACTIVE_BATCH_STATES.has(latest.status) || !state.batchId)', source)
        self.assertIn('localStorage.setItem("omniCaptureBatchId", state.batchId);', source)
        self.assertIn("if (currentBatch && !TERMINAL_BATCH_STATES.has(currentBatch.status)) return;", source)

    def test_terminal_or_missing_batch_rechecks_latest_during_polling(self):
        source = (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8")
        self.assertIn("await adoptLatestBatchIfCurrentUnavailable(batch);", source)
        self.assertIn("await adoptLatestBatchIfCurrentUnavailable();", source)


class WindowsOneClickRuntimeContractTests(unittest.TestCase):
    def test_legacy_start_cmd_delegates_to_api_and_emulator_entrypoint(self):
        source = (ROOT / "START.cmd").read_text(encoding="utf-8")
        lowered = source.lower()
        self.assertIn('call "%~dp0start_api_and_emulator.cmd" %*', lowered)
        self.assertIn('set "exit_code=%errorlevel%"', lowered)
        self.assertIn("exit /b %exit_code%", lowered)
        self.assertNotIn("setup.ps1", lowered)
        self.assertNotIn("start_facebook_capture_ui.ps1", lowered)

    def test_cmd_delegates_to_powershell_orchestrator_without_starting_a_batch(self):
        source = (ROOT / "START_API_AND_EMULATOR.cmd").read_text(encoding="utf-8")
        self.assertIn("start_api_and_emulator.ps1", source)
        self.assertIn("-NoProfile", source)
        self.assertIn("-ExecutionPolicy Bypass", source)
        self.assertIn("-File", source)
        self.assertIn("%~dp0", source)
        self.assertIn("pause", source.lower())
        self.assertIn("exit /b %exit_code%", source.lower())
        self.assertNotIn("/v1/jobs", source.lower())
        self.assertNotIn("/v1/batches", source.lower())
        self.assertNotIn("stop_facebook", source.lower())

    def test_orchestrator_reads_local_and_worker_configuration(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("facebook.local.json", source)
        self.assertIn("facebook_workers.json", source)
        self.assertIn("ConvertFrom-Json", source)
        self.assertIn("enabled", source)
        self.assertIn("workers", source)

    def test_orchestrator_initializes_dependencies_before_live_checks(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("setup.ps1", source)
        self.assertIn("-InstallOnly", source)

    def test_orchestrator_reuses_services_and_only_starts_missing_components(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-FacebookAdbState", source)
        self.assertIn("/health", source)
        self.assertIn("start_facebook_stable.ps1", source)
        self.assertIn("start_facebook_capture_api.ps1", source)
        self.assertIn("Start-Process", source)
        self.assertIn("-WindowStyle", source)
        self.assertIn("Hidden", source)
        self.assertIn("-Serial", source)
        self.assertIn("-AvdName", source)
        self.assertIn("-CapturePort", source)
        self.assertIn("-NativePort", source)

    def test_orchestrator_starts_or_reuses_v2_server_without_opening_browser(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("start_ui_v2.ps1", source)
        self.assertIn("-UiPort", source)
        self.assertIn("-ApiPort", source)
        self.assertIn("-NoBrowser", source)
        self.assertIn("8766", source)
        self.assertIn("-WindowStyle", source)
        self.assertIn("Hidden", source)

    def test_orchestrator_reuses_only_the_project_ui_on_8766(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-TcpPort -Port $uiPort", source)
        self.assertIn("Invoke-WebRequest", source)
        self.assertIn("http://127.0.0.1:8766/", source)
        self.assertIn("Port $uiPort is occupied", source)

    def test_queued_jobs_do_not_block_failed_worker_recovery(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertNotIn("[int]$queued -gt 0", source)
        self.assertIn("current_job_id", source)
        self.assertIn("worker", source)

    def test_orchestrator_clears_failed_worker_only_when_no_capture_is_running(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8")
        self.assertIn("/v1/workers/", source)
        self.assertIn("/enable", source)
        self.assertIn("current_job_id", source)
        self.assertIn("failed", source)
        self.assertIn("Invoke-RestMethod", source)

    def test_orchestrator_has_no_batch_mutation_or_delete_operations(self):
        source = (ROOT / "start_api_and_emulator.ps1").read_text(encoding="utf-8").lower()
        self.assertNotIn("/v1/jobs", source)
        self.assertNotIn("/v1/batches", source)
        self.assertNotIn("remove-item", source)
        self.assertIn("http://127.0.0.1:8766/", source)


if __name__ == "__main__":
    unittest.main()
