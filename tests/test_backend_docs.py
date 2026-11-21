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
import unittest
from pathlib import Path

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
        self.assertIn("## 后端 v1.0", readme)
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
        self.assertIn("--workers", dockerfile)
        self.assertIn('"1"', dockerfile)
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


class ReleaseConsistencyTests(unittest.TestCase):
    def test_release_notes_and_changelog_agree_on_the_version(self):
        from facebook_backend import __version__

        notes = (DOCS / f"RELEASE_NOTES_v{__version__}.md").read_text(encoding="utf-8")
        self.assertIn(f"v{__version__}", notes)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("Phase 8", changelog)
        self.assertIn("Phase 9", changelog)

    def test_repo_classification_matches_gitignore_intent(self):
        classification = (DOCS / "REPO_CLASSIFICATION.md").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("/runs/", "/data/", "/backups/", "/dist/", "/.venv/"):
            with self.subTest(entry=entry):
                self.assertIn(entry, ignore)
                self.assertIn(entry.strip("/"), classification)


if __name__ == "__main__":
    unittest.main()
