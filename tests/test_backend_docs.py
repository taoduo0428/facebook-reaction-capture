"""Documentation and packaging guards (Phase 9).

Docs rot silently, so the checks here are mechanical:

* every Phase 9 document exists and is not a stub;
* every relative markdown link in the backend-modernization docs and the README
  backend section resolves to a real file;
* the Docker files keep the two promises the deployment doc makes — loopback-only
  publishing and no device toolchain inside the image;
* the release notes and the CHANGELOG mention the same version.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.check_release_version import check_release_version

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "backend-modernization"

REQUIRED_DOCS = (
    "BASELINE.md",
    "ARCHITECTURE.md",
    "API_COMPATIBILITY.md",
    "DATABASE.md",
    "SCHEDULER.md",
    "WORKER.md",
    "DEPLOYMENT.md",
    "TESTING.md",
    "TROUBLESHOOTING.md",
    "PERF_REPORT.md",
    "REPO_CLASSIFICATION.md",
    "RELEASE.md",
    "STATE-STORE-CUTOVER.md",
    "RELEASE_NOTES_v1.0.0.md",
    "PHASE-01-FASTAPI-ADAPTER.md",
    "PHASE-02-MODULARIZATION.md",
    "PHASE-03-PERSISTENCE.md",
    "PHASE-04-SCHEDULER.md",
    "PHASE-05-RELIABILITY.md",
    "PHASE-06-OBSERVABILITY.md",
    "PHASE-07-CLIENT-COMPAT.md",
)


class DocumentSetTests(unittest.TestCase):
    def test_every_required_document_exists_and_is_not_a_stub(self):
        for name in REQUIRED_DOCS:
            with self.subTest(document=name):
                path = DOCS / name
                self.assertTrue(path.is_file(), f"missing document: {name}")
                self.assertGreater(len(path.read_text(encoding="utf-8")), 500, f"{name} looks empty")

    def test_relative_links_resolve(self):
        pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        missing: list[tuple[str, str]] = []
        for path in sorted(DOCS.glob("*.md")):
            for target in pattern.findall(path.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                relative = target.split("#", 1)[0].strip()
                if not relative:
                    continue
                if not (path.parent / relative).exists():
                    missing.append((path.name, relative))
        self.assertEqual(missing, [], f"broken relative links: {missing}")

    def test_readme_links_to_the_backend_docs(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("ARCHITECTURE", "DATABASE", "SCHEDULER", "DEPLOYMENT", "TESTING", "TROUBLESHOOTING"):
            with self.subTest(doc=name):
                self.assertIn(f"docs/backend-modernization/{name}.md", readme)


class DockerPackagingTests(unittest.TestCase):
    def test_image_has_no_device_toolchain_and_runs_one_worker(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        # Comments explain *why* the toolchain is absent; only instructions count.
        instructions = "\n".join(
            line for line in dockerfile.splitlines() if line.strip() and not line.strip().startswith("#")
        ).lower()
        for forbidden in ("adb ", "android-sdk", "emulator", "platform-tools"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, instructions)
        # v1.1 runs the read-only control plane in the image; the single-worker
        # rule is enforced inside the app (Settings/uvicorn workers=1), so the
        # guard checks the entrypoint owns that rule instead of a CLI flag.
        self.assertIn("facebook_backend.container", dockerfile)
        container = (ROOT / "facebook_backend" / "container.py").read_text(encoding="utf-8")
        self.assertIn("workers=1", container)
        self.assertIn("facebook_backend", dockerfile)

    def test_compose_publishes_loopback_only(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8765:8765"', compose)
        self.assertNotIn("0.0.0.0:8765", compose)
        self.assertIn("FACEBOOK_DB_URL", compose)

    def test_dockerignore_excludes_runtime_state(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for entry in ("runs", "data", "api-jobs", ".venv", "platform-tools"):
            with self.subTest(entry=entry):
                self.assertIn(entry, ignore)


class ProductizationConsistencyTests(unittest.TestCase):
    """The shipped docs must describe the shipped code, not an earlier version."""

    def read(self, *parts):
        return (DOCS.joinpath(*parts) if parts else DOCS).read_text(encoding="utf-8")

    def test_current_store_default_is_documented_from_the_code(self):
        from facebook_backend.config import Settings

        self.assertEqual(Settings(load_local_config=False).job_store, "auto")
        for name in ("DEPLOYMENT.md", "DATABASE.md", "ARCHITECTURE.md"):
            with self.subTest(document=name):
                text = self.read(name)
                self.assertIn("auto", text)
                self.assertNotIn("| `manifest` | FastAPI", text)

    def test_readme_describes_the_production_scheduler_and_limits(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for expected in (
            "ProductionScheduler",
            "NativeAttempt",
            "SQLite",
            "artifact_integrity",
            "max_concurrent_jobs=1",
            "只读控制面",
        ):
            with self.subTest(phrase=expected):
                self.assertIn(expected, readme)
        # The stale v1.0.1 claims must not come back.
        for stale in ("尚未接管执行", "默认以 manifest 保存任务和批次"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, readme)

    def test_deployment_documents_the_real_upgrade_and_docker_boundary(self):
        text = self.read("DEPLOYMENT.md")
        for command in ("--dry-run", "--apply", "--verify", "--rollback", "alembic upgrade head"):
            with self.subTest(command=command):
                self.assertIn(command, text)
        self.assertIn("只读控制面", text)
        self.assertIn("本机没有安装 Docker", text)

    def test_testing_separates_evidence_layers_and_lists_acceptance_items(self):
        text = self.read("TESTING.md")
        for layer in ("CI", "Mock", "真实操作系统", "真实 Android 设备"):
            with self.subTest(layer=layer):
                self.assertIn(layer, text)
        for item in (
            "ADB 设备状态", "API Health", "Worker Health", "Device Lease", "Job Submit",
            "Job Progress", "Job Complete", "Artifact Download", "SHA-256 验证", "Batch Summary",
            "Cancellation", "Process Stop Verification", "Service Restart Recovery",
            "Worker Failover", "Transport Cleanup",
        ):
            with self.subTest(item=item):
                self.assertIn(item, text)
        self.assertIn("1323", text)
        self.assertIn("1330", text)

    def test_architecture_review_keeps_all_ten_diagrams(self):
        text = self.read("V1.1-FINAL-ARCHITECTURE-REVIEW.md")
        for title in (
            "Overall Architecture", "API Request Flow", "Job Lifecycle", "Batch Lifecycle",
            "Scheduler Flow", "Device Lease Lifecycle", "Worker Failover", "Process Recovery",
            "Artifact Lifecycle", "Observability Trace",
        ):
            with self.subTest(title=title):
                self.assertIn(title, text)
        self.assertGreaterEqual(text.count("```mermaid"), 10)

    def test_changelog_carries_the_current_version(self):
        from facebook_backend import __version__

        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## v{__version__}", text)

class ReleaseConsistencyTests(unittest.TestCase):
    def test_checkout_and_fastapi_metadata_agree(self):
        from facebook_backend import __version__, create_app
        from facebook_backend.config import Settings

        self.assertEqual(check_release_version(ROOT), f"v{__version__}")
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(base_dir=Path(tmp), load_local_config=False)
            app = create_app(manager=object(), settings=settings)
            self.assertEqual(app.openapi()["info"]["version"], __version__)

    def test_tag_cli_checks_the_value_supplied_by_the_caller(self):
        from facebook_backend import __version__

        expected = f"v{__version__}"
        with tempfile.TemporaryDirectory() as tmp:
            for args, code in (([], 0), (["--tag", expected], 0), (["--tag", expected + "0"], 1),
                               (["--tag", ""], 1), (["--tag", "main"], 1)):
                with self.subTest(args=args):
                    result = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "tools/check_release_version.py"), *args],
                        cwd=tmp, capture_output=True, encoding="utf-8", timeout=15,
                    )
                    self.assertEqual(result.returncode, code, result.stderr)
                    if code:
                        self.assertIn("Git tag", result.stderr)

    def test_branch_check_rejects_drift_even_when_old_version_is_in_history(self):
        notes = "docs/backend-modernization/RELEASE_NOTES_v2.3.4.md"
        files = {
            "facebook_backend/__init__.py": '__version__ = "2.3.4"\n',
            "README.md": f"## 后端 v2.3.4\n[notes]({notes})\n",
            "CHANGELOG.md": "## v2.3.4 — current\n## v1.0.0 — historical\n",
            notes: "# v2.3.4 — release notes\nBased on v1.0.0\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            self.assertEqual(check_release_version(root), "v2.3.4")
            for name in files:
                with self.subTest(file=name):
                    path = root / name
                    path.write_text(files[name].replace("2.3.4", "1.0.0", 1), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        check_release_version(root)
                    path.write_text(files[name], encoding="utf-8")
            (root / notes).unlink()
            with self.assertRaises(FileNotFoundError):
                check_release_version(root)

    def test_repo_classification_matches_gitignore_intent(self):
        classification = (DOCS / "REPO_CLASSIFICATION.md").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("/runs/", "/data/", "/backups/", "/dist/", "/.venv/"):
            with self.subTest(entry=entry):
                self.assertIn(entry, ignore)
                self.assertIn(entry.strip("/"), classification)


if __name__ == "__main__":
    unittest.main()
