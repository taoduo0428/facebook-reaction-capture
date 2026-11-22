"""Phase 2 modularization guards.

The refactor moved real logic out of ``facebook_capture_api`` into
``facebook_backend.{domain,platform,artifacts,persistence,scheduler}`` while the
legacy module kept the same names as thin delegating wrappers.

These tests prove three things:

1. **One implementation** — patching the new module changes the legacy symbol's
   result, so the legacy name cannot be a stale copy.
2. **Shared type identity** — ``ApiError`` / ``WorkerConfig`` are the *same*
   objects in both modules, so ``isinstance`` checks keep working everywhere.
3. **Unchanged behaviour** — the moved rules (path boundary, artifact states,
   worker validation, atomic write format, request normalisation) behave exactly
   as before.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import facebook_capture_api as api
from facebook_backend import domain
from facebook_backend.artifacts import catalog as artifact_catalog
from facebook_backend.artifacts import manager as artifact_manager
from facebook_backend.domain import normalization
from facebook_backend.persistence import atomic_write
from facebook_backend.platform import process_manager
from facebook_backend.scheduler import device_registry


class SingleImplementationTests(unittest.TestCase):
    """The legacy names must delegate, not re-implement."""

    def test_process_primitives_delegate(self):
        with mock.patch.object(process_manager, "creation_time", return_value=123.5):
            self.assertEqual(api._process_creation_time(4242), 123.5)
        with mock.patch.object(process_manager, "presence", return_value=True):
            self.assertIs(api._process_presence(4242), True)
        with mock.patch.object(process_manager, "terminate_tree", return_value=False):
            self.assertIs(api._terminate_tree(mock.Mock()), False)

    def test_process_coercions_delegate(self):
        with mock.patch.object(process_manager, "coerce_pid", return_value=7):
            self.assertEqual(api._coerce_process_pid("x"), 7)
        with mock.patch.object(process_manager, "coerce_creation_time", return_value=9.5):
            self.assertEqual(api._coerce_process_creation_time("x"), 9.5)

    def test_artifact_helpers_delegate(self):
        with mock.patch.object(artifact_manager, "checked_artifact_path", return_value=Path("sentinel")):
            self.assertEqual(api._checked_artifact_path(Path("."), Path("a")), Path("sentinel"))
        with mock.patch.object(artifact_manager, "artifact_metadata", return_value=[{"name": "x"}]):
            self.assertEqual(api._artifact_metadata(Path("."), "queued"), [{"name": "x"}])
        with mock.patch.object(artifact_manager, "required_artifacts_ready", return_value=True):
            self.assertIs(api._required_artifacts_ready(Path(".")), True)

    def test_atomic_writers_delegate(self):
        with mock.patch.object(atomic_write, "write_json") as writer:
            api._atomic_write_json(Path("x.json"), {"a": 1})
        writer.assert_called_once_with(Path("x.json"), {"a": 1})
        with mock.patch.object(atomic_write, "write_csv") as writer:
            api._atomic_write_csv(Path("x.csv"), ["a"], [{"a": 1}])
        writer.assert_called_once_with(Path("x.csv"), ["a"], [{"a": 1}])

    def test_worker_registry_delegates(self):
        with mock.patch.object(device_registry, "default_worker", return_value="sentinel"):
            self.assertEqual(api._default_worker("emulator-9"), "sentinel")
        with mock.patch.object(device_registry, "validate_worker_config", return_value=[]):
            self.assertEqual(api.validate_worker_config([]), [])

    def test_load_worker_config_passes_the_module_base_dir(self):
        with mock.patch.object(device_registry, "load_worker_config", return_value=[]) as loader:
            api.load_worker_config(Path("cfg.json"), "emulator-1")
        loader.assert_called_once_with(Path("cfg.json"), "emulator-1", base_dir=api.BASE_DIR)

    def test_identity_checks_still_resolve_through_the_legacy_symbol(self):
        # 924 existing tests patch ``facebook_capture_api._process_creation_time``;
        # the composition inside _process_identity_state must keep using it.
        with mock.patch.object(api, "_process_creation_time", return_value=100.5):
            self.assertEqual(api._process_identity_state(123, 100.0), "match")
            self.assertEqual(api._process_identity_state(123, 500.0), "mismatch")
        with mock.patch.object(api, "_process_creation_time", return_value=None):
            with mock.patch.object(api, "_process_presence", return_value=False):
                self.assertEqual(api._process_identity_state(123, 100.0), "absent")
            with mock.patch.object(api, "_process_presence", return_value=None):
                self.assertEqual(api._process_identity_state(123, 100.0), "unknown")


class SharedIdentityTests(unittest.TestCase):
    def test_error_types_are_the_same_objects(self):
        self.assertIs(api.ApiError, domain.exceptions.ApiError)
        self.assertIs(api.SchedulerStartError, domain.exceptions.SchedulerStartError)
        self.assertIs(api.WorkerUnavailableError, domain.exceptions.WorkerUnavailableError)
        self.assertTrue(issubclass(api.WorkerUnavailableError, api.ApiError))

    def test_worker_config_is_the_same_class(self):
        self.assertIs(api.WorkerConfig, device_registry.WorkerConfig)
        self.assertIs(api.DEFAULT_SERIAL, device_registry.DEFAULT_SERIAL)

    def test_normalizers_are_the_same_functions(self):
        self.assertIs(api.normalize_request, normalization.normalize_request)
        self.assertIs(api.normalize_batch_request, normalization.normalize_batch_request)
        self.assertIs(api.normalize_post_url, normalization.normalize_post_url)

    def test_catalogue_constants_are_shared(self):
        self.assertEqual(api.ARTIFACT_SPECS, artifact_catalog.ARTIFACT_SPECS)
        self.assertEqual(api.ARTIFACT_PATHS, artifact_catalog.ARTIFACT_PATHS)
        self.assertEqual(api.REQUIRED_ARTIFACTS, artifact_catalog.REQUIRED_ARTIFACTS)
        self.assertEqual(api.DEFAULT_SAVE_ARTIFACTS, artifact_catalog.DEFAULT_SAVE_ARTIFACTS)
        self.assertEqual(api.BATCH_ARTIFACT_SPECS, artifact_catalog.BATCH_ARTIFACT_SPECS)


class NoDuplicatedImplementationTests(unittest.TestCase):
    """Cheap drift guard: the moved code must not still live in the old module."""

    def test_legacy_module_no_longer_contains_the_moved_implementations(self):
        source = Path(api.__file__).read_text(encoding="utf-8")
        for marker, owner in (
            ("kernel32", "facebook_backend/platform/process_manager.py"),
            ("GetProcessTimes", "facebook_backend/platform/process_manager.py"),
            ("reaction_scroll_limit must be an integer", "facebook_backend/domain/normalization.py"),
            ("artifact path is outside its run directory", "facebook_backend/artifacts/manager.py"),
            ("worker configuration must contain at least one enabled worker",
             "facebook_backend/scheduler/device_registry.py"),
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source, f"{marker} should live in {owner}")


class ArtifactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "analysis").mkdir()
        (self.root / "analysis" / "facebook-reactor-users.csv").write_text("user_id\n1\n", encoding="utf-8")

    def test_accepts_a_confined_non_empty_file(self):
        result = artifact_manager.checked_artifact_path(
            self.root, Path("analysis/facebook-reactor-users.csv")
        )
        self.assertEqual(result, self.root / "analysis" / "facebook-reactor-users.csv")

    def test_rejects_parent_traversal_and_absolute_paths(self):
        for relative in (Path("../escape.csv"), Path("analysis/../../escape.csv")):
            with self.subTest(relative=relative):
                with self.assertRaises(api.ApiError):
                    artifact_manager.checked_artifact_path(self.root, relative)

    def test_rejects_empty_file(self):
        (self.root / "empty.json").write_text("", encoding="utf-8")
        with self.assertRaises(api.ApiError):
            artifact_manager.checked_artifact_path(self.root, Path("empty.json"))

    def test_rejects_missing_file_with_the_unavailable_message(self):
        with self.assertRaises(api.ApiError) as caught:
            artifact_manager.checked_artifact_path(self.root, Path("nope.csv"))
        self.assertIn("not available", str(caught.exception))

    def test_rejects_symlinked_component(self):
        target = self.root / "analysis" / "facebook-reactor-users.csv"
        link = self.root / "link.csv"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks are not permitted in this environment")
        with self.assertRaises(api.ApiError) as caught:
            artifact_manager.checked_artifact_path(self.root, Path("link.csv"))
        self.assertIn("linked", str(caught.exception))

    def test_required_artifacts_rule(self):
        self.assertFalse(artifact_manager.required_artifacts_ready(self.root))
        (self.root / "analysis" / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
        self.assertTrue(artifact_manager.required_artifacts_ready(self.root))
        (self.root / "analysis" / "facebook-sample-patterns.json").write_text("", encoding="utf-8")
        self.assertFalse(artifact_manager.required_artifacts_ready(self.root))

    def test_artifact_metadata_states(self):
        rows = artifact_manager.artifact_metadata(self.root, "queued", ["safe_capture"])
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["facebook-reactor-users.csv"]["state"], "writing")
        self.assertEqual(by_name["raw-network.pcap"]["state"], "discarding")
        completed = artifact_manager.artifact_metadata(self.root, "completed", ["safe_capture"])
        by_name = {row["name"]: row for row in completed}
        self.assertEqual(by_name["facebook-sample-patterns.json"]["state"], "missing")
        self.assertEqual(by_name["facebook-reactor-users.csv"]["state"], "ready")


class WorkerRegistryTests(unittest.TestCase):
    def test_missing_file_falls_back_to_a_single_enabled_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            workers = device_registry.load_worker_config(base_dir=Path(tmp))
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0].worker_id, "worker-01")
        self.assertTrue(workers[0].enabled)
        self.assertEqual(workers[0].cooldown_seconds, device_registry.DEFAULT_COOLDOWN_SECONDS)

    def test_validation_rejects_duplicate_ports(self):
        workers = [
            device_registry.WorkerConfig("w1", "emulator-1", "avd1", 8081, 18766, True),
            device_registry.WorkerConfig("w2", "emulator-2", "avd2", 8081, 18767, True),
        ]
        with self.assertRaises(api.ApiError):
            device_registry.validate_worker_config(workers)

    def test_validation_rejects_no_enabled_worker(self):
        with self.assertRaises(api.ApiError):
            device_registry.validate_worker_config(
                [device_registry.WorkerConfig("w1", "emulator-1", "avd1", 8081, 18766, False)]
            )

    def test_round_trip_through_the_legacy_writer(self):
        workers = [device_registry.WorkerConfig("w1", "emulator-1", "avd1", 8081, 18766, True)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workers.json"
            api.write_worker_config(path, workers)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["workers"][0]["worker_id"], "w1")
            self.assertEqual(len(api.load_worker_config(path)), 1)


class AtomicWriteTests(unittest.TestCase):
    def test_json_is_compact_utf8_with_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.json"
            atomic_write.write_json(path, {"name": "张三", "n": 1})
            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertIn("张三".encode("utf-8"), raw)
            self.assertNotIn(b" ", raw)
            self.assertEqual(json.loads(raw.decode("utf-8")), {"name": "张三", "n": 1})

    def test_csv_keeps_the_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            atomic_write.write_csv(path, ["user_id", "name"], [{"user_id": "1", "name": "张三"}])
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn("张三".encode("utf-8"), raw)

    def test_no_temporary_files_are_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.json"
            atomic_write.write_json(path, {"a": 1})
            atomic_write.write_json(path, {"a": 2})
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["job.json"])


class NormalizationBehaviourTests(unittest.TestCase):
    def test_accepts_a_facebook_post_url(self):
        request = normalization.normalize_request({"post_url": "https://www.facebook.com/x/posts/1"})
        self.assertEqual(request["collect"], ["reactions"])
        self.assertEqual(request["save_artifacts"], ["safe_capture", "progress"])
        self.assertIsNone(request["reaction_scroll_limit"])
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_rejects_foreign_hosts(self):
        with self.assertRaises(api.ApiError) as caught:
            normalization.normalize_request({"post_url": "https://evil.example/posts/1"})
        self.assertEqual(str(caught.exception), "post_url must be a Facebook URL")

    def test_bounded_sample_marks_explicitness(self):
        request = normalization.normalize_request(
            {"post_url": "https://www.facebook.com/x/posts/1", "reaction_scroll_limit": 50}
        )
        self.assertEqual(request["reaction_scroll_limit"], 50)
        self.assertTrue(request["reaction_scroll_limit_explicit"])

    def test_limit_range_is_enforced(self):
        for value in (0, 301, True, "50"):
            with self.subTest(value=value):
                with self.assertRaises(api.ApiError):
                    normalization.normalize_reaction_scroll_limit(value)

    def test_legacy_manifest_without_the_explicitness_bit_stays_unlimited(self):
        request = normalization.normalize_persisted_request(
            {"post_url": "https://www.facebook.com/x/posts/1", "reaction_scroll_limit": 60}
        )
        self.assertIsNone(request["reaction_scroll_limit"])
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_batch_deduplicates_and_caps(self):
        request = normalization.normalize_batch_request(
            {"post_urls": [
                "https://www.facebook.com/x/posts/1",
                "https://www.facebook.com/x/posts/1",
                "https://www.facebook.com/x/posts/2",
            ]}
        )
        self.assertEqual(len(request["post_urls"]), 2)
        too_many = [f"https://www.facebook.com/x/posts/{i}" for i in range(101)]
        with self.assertRaises(api.ApiError):
            normalization.normalize_batch_request({"post_urls": too_many})


if __name__ == "__main__":
    unittest.main()
