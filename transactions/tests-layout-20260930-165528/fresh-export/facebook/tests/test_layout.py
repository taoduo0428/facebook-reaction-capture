"""Regression guards for source-only test relocation and CI entrypoints."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestLayoutTests(unittest.TestCase):
    def test_python_tests_are_packaged_not_left_at_project_root(self):
        self.assertFalse(list(ROOT.glob("test_*.py")))
        self.assertTrue((ROOT / "tests" / "__init__.py").is_file())
        self.assertTrue((ROOT / "tests" / "test_facebook_capture_api.py").is_file())

    def test_release_manifest_includes_all_python_and_manual_tests(self):
        manifest = set((ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines())
        required = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
        required.update({"tests/__init__.py", "tests/README.md"})
        required.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests" / "manual").glob("test_*.ps1"))
        self.assertFalse(required - manifest)
        self.assertFalse(any(name.startswith("test_") for name in manifest))

    def test_ci_unittest_targets_resolve_from_package(self):
        source = (ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        targets = []
        for line in source.splitlines():
            if "-m unittest " in line:
                targets.extend(line.split("-m unittest ", 1)[1].split())
        self.assertTrue(targets)
        self.assertTrue(all(name.startswith("tests.test_") for name in targets))
        loader = unittest.TestLoader()
        loader.loadTestsFromNames(sorted(set(targets)))
        self.assertEqual(loader.errors, [])


if __name__ == "__main__":
    unittest.main()
