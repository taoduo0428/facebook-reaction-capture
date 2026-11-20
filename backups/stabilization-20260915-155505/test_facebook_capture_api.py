import csv
import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import facebook_capture_api as api


class WorkerConfigTests(unittest.TestCase):
    def _write_config(self, path: Path, workers: list[dict]):
        path.write_text(json.dumps({"version": 1, "workers": workers}), encoding="utf-8")

    def test_default_workers_preserve_primary_and_disable_secondary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "facebook_workers.json"
            self._write_config(path, [
                {
                    "worker_id": "worker-01",
                    "serial": "emulator-5554",
                    "avd_name": "fb-x86_64-api35",
                    "capture_port": 8081,
                    "native_port": 18766,
                    "enabled": True,
                },
                {
                    "worker_id": "worker-02",
                    "serial": "emulator-5556",
                    "avd_name": "fb-x86_64-api35-worker-02",
                    "capture_port": 18082,
                    "native_port": 18767,
                    "enabled": False,
                },
            ])
            workers = api.load_worker_config(path)
        self.assertEqual(workers[0].serial, "emulator-5554")
        self.assertEqual(workers[0].avd_name, "fb-x86_64-api35")
        self.assertTrue(workers[0].enabled)
        self.assertEqual(workers[1].serial, "emulator-5556")
        self.assertFalse(workers[1].enabled)
        self.assertFalse(workers[1].frida_ssl_unpin)

    def test_worker_ports_and_identity_must_be_unique(self):
        duplicate_ports = [
            api.WorkerConfig("worker-01", "emulator-5554", "first", 8081, 18766, True),
            api.WorkerConfig("worker-02", "emulator-5556", "second", 8081, 18767, True),
        ]
        with self.assertRaises(api.ApiError):
            api.validate_worker_config(duplicate_ports)

    def test_worker_config_requires_an_enabled_worker(self):
        workers = [api.WorkerConfig("worker-02", "emulator-5556", "second", 18082, 18767, False)]
        with self.assertRaises(api.ApiError):
            api.validate_worker_config(workers)


@mock.patch('facebook_capture_api.sys.platform', 'win32')
class WorkerCommandTests(unittest.TestCase):
    @mock.patch("facebook_capture_api.shutil.which")
    def test_capture_command_uses_worker_specific_ports(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(
            request,
            serial="emulator-5556",
            proxy_port=18082,
            native_port=18767,
        )
        self.assertEqual(command[command.index("-Serial") + 1], "emulator-5556")
        self.assertEqual(command[command.index("-ProxyPort") + 1], "18082")
        self.assertEqual(command[command.index("-ReactionNativeScrollPort") + 1], "18767")

    @mock.patch("facebook_capture_api.shutil.which")
    def test_secondary_worker_can_enable_isolated_frida_unpin(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(
            request,
            serial="emulator-5556",
            proxy_port=18082,
            native_port=18767,
            frida_ssl_unpin=True,
        )
        self.assertIn("-EnableFridaSslUnpin", command)

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch("facebook_capture_api.subprocess.Popen")
    @mock.patch("facebook_capture_api.shutil.which", return_value="powershell")
    @mock.patch("facebook_capture_api.adb_snapshot")
    def test_emulator_bootstrap_receives_exact_worker_identity_and_ports(self, snapshot, _which, popen, _sleep):
        snapshot.side_effect = [
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ]
        worker = api.WorkerConfig(
            "worker-02", "emulator-5556", "fb-x86_64-api35-worker-02", 18082, 18767, False
        )
        api.ensure_emulator(worker=worker, timeout_seconds=2)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-Serial") + 1], "emulator-5556")
        self.assertEqual(command[command.index("-AvdName") + 1], "fb-x86_64-api35-worker-02")
        self.assertEqual(command[command.index("-CapturePort") + 1], "18082")
        self.assertEqual(command[command.index("-NativePort") + 1], "18767")

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch("facebook_capture_api.subprocess.Popen")
    @mock.patch("facebook_capture_api.shutil.which", return_value="powershell")
    @mock.patch("facebook_capture_api.adb_snapshot")
    def test_frida_worker_restores_mitm_certificate_when_bootstrapped(self, snapshot, _which, popen, _sleep):
        snapshot.side_effect = [
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ]
        worker = api.WorkerConfig(
            "worker-03", "emulator-5558", "fb-x86_64-api35-worker-03", 18083, 18768, False,
            frida_ssl_unpin=True,
        )
        api.ensure_emulator(worker=worker, timeout_seconds=2)
        self.assertIn("-RestoreMitmCertificate", popen.call_args.args[0])


class MultiWorkerManagerTests(unittest.TestCase):
    def setUp(self):
        self.workers = [
            api.WorkerConfig("worker-01", "emulator-5554", "first", 8081, 18766, True),
            api.WorkerConfig("worker-02", "emulator-5556", "second", 18082, 18767, False),
        ]

    def _manager(self, base: Path):
        base_patch = mock.patch.object(api, "BASE_DIR", base)
        base_patch.start()
        self.addCleanup(base_patch.stop)
        start_patch = mock.patch("facebook_capture_api.threading.Thread.start")
        started = start_patch.start()
        self.addCleanup(start_patch.stop)
        return api.JobManager(workers=self.workers), started

    def test_disabled_worker_does_not_start_or_claim_work(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, started = self._manager(Path(temp))
            self.assertEqual(started.call_count, 1)
            self.assertEqual(set(manager._started_worker_ids), {"worker-01"})

    def test_claim_persists_worker_ownership_and_rejects_cross_worker_reclaim(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))
            public = manager.public_job(job_id)
            self.assertEqual(public["worker_id"], "worker-01")
            self.assertEqual(public["serial"], "emulator-5554")
            manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["worker_id"], "worker-01")
            self.assertEqual(manifest["serial"], "emulator-5554")

    def test_two_enabled_workers_use_one_serial_active_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            first = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            second = manager.submit({"post_url": "https://www.facebook.com/example/posts/2"})
            self.assertTrue(manager._assign_job_to_worker(first["job_id"], self.workers[0]))
            self.assertFalse(manager._assign_job_to_worker(second["job_id"], self.workers[1]))
            self.assertEqual(manager.public_job(first["job_id"])["worker_id"], "worker-01")
            self.assertIsNone(manager.public_job(second["job_id"])["worker_id"])

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "offline", "boot_completed": False, "proxy": ""},
    )
    def test_two_enabled_workers_start_one_scheduler_and_keep_second_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, started = self._manager(Path(temp))
            self.assertEqual(started.call_count, 1)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertEqual(manager._started_worker_ids, {"worker-01"})
            self.assertEqual(manager.worker_health("worker-02")["state"], "standby")

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "offline", "boot_completed": False, "proxy": ""},
    )
    def test_confirmed_primary_failure_requeues_from_checkpoint_to_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(base)
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            job_id = submitted["job_id"]
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[0]))
            job = manager._jobs[job_id]
            attempt = Path(job["capture_run_dir"])
            analysis = attempt / "analysis"
            analysis.mkdir(parents=True)
            with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(api.REACTOR_USER_FIELDS))
                writer.writeheader()
                writer.writerow({"user_id": "u1", "user_name": "One", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE"})
            self.assertTrue(manager._recover_after_worker_failure(job_id, self.workers[0], api.ApiError("ADB disconnected")))
            self.assertEqual(manager._active_worker_id, "worker-02")
            self.assertEqual(manager._jobs[job_id]["status"], "queued")
            self.assertEqual(manager._jobs[job_id]["recovery_state"], "checkpointed-queued-for-standby")
            self.assertEqual(manager._jobs[job_id]["checkpoint"]["preserved_user_count"], 1)
            checkpoint = base / "runs" / f"api-{job_id}" / "checkpoint" / "facebook-reactor-users.csv"
            self.assertTrue(checkpoint.exists())
            with checkpoint.open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))
            manager._active_job_id = None
            manager._worker_runtime["worker-01"]["current_job_id"] = None
            self.assertTrue(manager._assign_job_to_worker(job_id, self.workers[1]))
            self.assertTrue(manager._jobs[job_id]["capture_run_dir"].endswith("attempt-002"))

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
    )
    def test_online_capture_error_does_not_switch_to_standby(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            self.assertTrue(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))
            result = manager._recover_after_worker_failure(
                submitted["job_id"], self.workers[0], api.ApiError("capture command failed with exit 2; rate-limit")
            )
            self.assertIsNone(result)
            self.assertEqual(manager._active_worker_id, "worker-01")
            self.assertNotIn("worker-01", manager._failed_worker_ids)

    @mock.patch("facebook_capture_api._process_creation_time", return_value=999.0)
    def test_restart_with_unverified_capture_pid_blocks_all_workers(self, _creation_time):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartpid"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(base / "runs" / f"api-{job_id}"),
                "capture_run_dir": str(base / "runs" / f"api-{job_id}" / "attempt-001"),
                "attempt_dirs": [str(base / "runs" / f"api-{job_id}" / "attempt-001")],
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": 424242,
                "process_creation_time": 123.0,
                "process_state": "running",
            }
            manifest_path = base / "api-jobs" / f"{job_id}.job.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            manager, started = self._manager(base)

            self.assertTrue(manager._capture_lease_uncertain)
            self.assertIsNone(manager._active_worker_id)
            self.assertEqual(started.call_count, 0)
            self.assertEqual(manager.public_job(job_id)["process_state"], "restart-unverified")
            self.assertFalse(manager._assign_job_to_worker(job_id, self.workers[1]))

    @mock.patch("facebook_capture_api.time.sleep")
    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        side_effect=[
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ],
    )
    def test_worker_failure_probe_retries_transient_adb_failure(self, snapshot, sleep):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))

            self.assertFalse(manager._worker_failure_confirmed(self.workers[0], api.ApiError("capture failed")))
            self.assertEqual(snapshot.call_count, 2)
            sleep.assert_called_once_with(0.25)

    def test_materialize_attempts_preserves_and_deduplicates_users(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job"
            attempts = [root / "attempt-001", root / "attempt-002"]
            rows = [
                {"user_id": "u1", "user_name": "One", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE", "line": "7", "original_content_id": "content-1"},
                {"user_id": "u2", "user_name": "Two", "avatar_url": "", "source_post_id": "p1", "reaction_id": "LIKE", "source_field": "viewer_reaction", "original_content_id": "content-1"},
            ]
            for attempt in attempts:
                analysis = attempt / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[*api.REACTOR_USER_FIELDS, "line", "source_field", "original_content_id"],
                    )
                    writer.writeheader()
                    for row in rows[:1] if attempt == attempts[0] else rows:
                        writer.writerow(row)
            api.materialize_capture_attempts(root, attempts)
            with (root / "analysis" / "facebook-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                merged = list(csv.DictReader(handle))
            self.assertEqual([row["user_id"] for row in merged], ["u1", "u2"])
            extra_fields = (set(merged[0]) | set(merged[1])) - set(api.REACTOR_USER_FIELDS)
            self.assertTrue({"line", "source_field", "original_content_id"} <= extra_fields)
            self.assertEqual(len(api._read_reactor_rows_file(root / "checkpoint" / "facebook-reactor-users.csv")), 2)

    @mock.patch("facebook_capture_api.time.time", return_value=100.0)
    def test_cooling_worker_does_not_claim_a_new_post(self, _time):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))
            submitted = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            manager._worker_runtime["worker-01"]["cooldown_until"] = 101.0
            self.assertFalse(manager._assign_job_to_worker(submitted["job_id"], self.workers[0]))
            self.assertEqual(manager.public_job(submitted["job_id"])["status"], "queued")

    @mock.patch("facebook_capture_api.adb_snapshot", return_value={"state": "offline", "boot_completed": False, "proxy": ""})
    def test_health_lists_enabled_and_disabled_workers_without_account_identity(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            manager, _started = self._manager(Path(temp))
            health = manager.health()
            self.assertEqual([item["worker_id"] for item in health["workers"]], ["worker-01", "worker-02"])
            self.assertEqual(health["workers"][0]["state"], "offline")
            self.assertEqual(health["workers"][1]["state"], "disabled")
            self.assertNotIn("account", json.dumps(health).lower())

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        side_effect=[
            {"state": "offline", "boot_completed": False, "proxy": ""},
            {"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
        ],
    )
    def test_health_top_level_android_follows_active_worker(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            self.workers[1] = replace(self.workers[1], enabled=True)
            manager, _started = self._manager(Path(temp))
            manager._active_worker_id = "worker-02"

            health = manager.health()

            self.assertEqual(health["active_worker_id"], "worker-02")
            self.assertEqual(health["serial"], "emulator-5556")
            self.assertEqual(health["android"]["state"], "device")

    @mock.patch(
        "facebook_capture_api.adb_snapshot",
        return_value={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
    )
    def test_reload_workers_adds_disabled_worker_then_allows_enable(self, _snapshot):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, started = self._manager(base)
            third = api.WorkerConfig(
                "worker-03", "emulator-5558", "third", 18083, 18768, False, frida_ssl_unpin=True
            )
            api.write_worker_config(base / "facebook_workers.json", [*self.workers, third])

            result = manager.reload_workers()

            self.assertEqual(result["added"], ["worker-03"])
            self.assertIn("worker-03", manager.workers)
            self.assertEqual(manager._worker_runtime["worker-03"]["current_job_id"], None)
            self.assertNotIn("worker-03", manager._started_worker_ids)
            manager.set_worker_enabled("worker-03", True)
            self.assertNotIn("worker-03", manager._started_worker_ids)
            self.assertEqual(started.call_count, 1)
            self.assertEqual(manager.worker_health("worker-03")["state"], "standby")

    def test_reload_workers_rejects_existing_identity_change(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            changed = replace(self.workers[0], serial="emulator-5560")
            api.write_worker_config(base / "facebook_workers.json", [changed, self.workers[1]])

            with self.assertRaisesRegex(api.ApiError, "immutable identity"):
                manager.reload_workers()

            self.assertEqual(manager.workers["worker-01"].serial, "emulator-5554")

    def test_reload_workers_rejects_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager, _started = self._manager(base)
            api.write_worker_config(base / "facebook_workers.json", [self.workers[0]])

            with self.assertRaisesRegex(api.ApiError, "remove"):
                manager.reload_workers()

            self.assertEqual(set(manager.workers), {"worker-01", "worker-02"})


class RequestTests(unittest.TestCase):
    def test_normalize_request_defaults_to_reactions(self):
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        self.assertEqual(request["collect"], ["reactions"])
        self.assertEqual(request["post_id"], "")
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_defaults_to_unlimited_and_accepts_boundaries(self):
        default_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        self.assertEqual(default_request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(default_request["reaction_scroll_limit_explicit"])
        for value in (api.REACTION_SCROLL_LIMIT_MIN, api.REACTION_SCROLL_LIMIT_MAX):
            with self.subTest(value=value):
                request = api.normalize_request({
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "reaction_scroll_limit": value,
                })
                self.assertEqual(request["reaction_scroll_limit"], value)
                self.assertTrue(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_null_is_unlimited(self):
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": None,
        })
        self.assertIsNone(request["reaction_scroll_limit"])
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_reaction_scroll_limit_rejects_invalid_values(self):
        for value in (0, api.REACTION_SCROLL_LIMIT_MAX + 1, -1, 1.5, True, "3"):
            with self.subTest(value=value):
                with self.assertRaises(api.ApiError):
                    api.normalize_request({
                        "post_url": "https://www.facebook.com/example/posts/1",
                        "reaction_scroll_limit": value,
                    })

    def test_normalize_url_decodes_u0026(self):
        request = api.normalize_request({"post_url": "https://m.facebook.com/story.php?story_fbid=1\\u0026id=2"})
        self.assertIn("&id=2", request["post_url"])

    def test_rejects_non_facebook_url(self):
        with self.assertRaises(api.ApiError):
            api.normalize_request({"post_url": "https://example.com/post/1"})

    def test_save_artifacts_defaults_to_lightweight_and_accepts_empty_selection(self):
        default_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        minimal_request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1", "save_artifacts": []})
        self.assertEqual(default_request["save_artifacts"], ["safe_capture", "progress"])
        self.assertEqual(default_request["save_artifacts"], list(api.DEFAULT_SAVE_ARTIFACTS))
        self.assertEqual(minimal_request["save_artifacts"], [])

    def test_rejects_unknown_artifact_group(self):
        with self.assertRaises(api.ApiError):
            api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1", "save_artifacts": ["unknown"]})

    def test_normalize_batch_request_deduplicates_urls_and_keeps_order(self):
        request = api.normalize_batch_request({
            "post_urls": [
                "https://www.facebook.com/example/posts/1",
                "https://www.facebook.com/example/posts/2",
                "https://www.facebook.com/example/posts/1",
            ],
            "save_artifacts": ["safe_capture"],
            "reaction_scroll_limit": api.REACTION_SCROLL_LIMIT_MAX,
        })
        self.assertEqual(request["post_urls"], [
            "https://www.facebook.com/example/posts/1",
            "https://www.facebook.com/example/posts/2",
        ])
        self.assertEqual(request["save_artifacts"], ["safe_capture"])
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_MAX)
        self.assertTrue(request["reaction_scroll_limit_explicit"])

    def test_normalize_batch_request_marks_default_limit_as_not_explicit(self):
        request = api.normalize_batch_request({
            "post_urls": ["https://www.facebook.com/example/posts/1"],
        })
        self.assertEqual(request["reaction_scroll_limit"], api.REACTION_SCROLL_LIMIT_DEFAULT)
        self.assertFalse(request["reaction_scroll_limit_explicit"])

    def test_normalize_batch_request_rejects_empty_and_over_limit_batches(self):
        with self.assertRaises(api.ApiError):
            api.normalize_batch_request({"post_urls": []})
        with self.assertRaises(api.ApiError):
            api.normalize_batch_request({
                "post_urls": [f"https://www.facebook.com/example/posts/{index}" for index in range(api.MAX_BATCH_POSTS + 1)]
            })

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_supported_facebook_link_families_are_forwarded_without_special_cases(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        urls = (
            "https://www.facebook.com/share/p/genericSharePost/",
            "https://www.facebook.com/share/r/genericShareReel/",
            "https://www.facebook.com/groups/123456789/permalink/987654321/",
            "https://www.facebook.com/example/posts/pfbidGenericPost",
            "https://www.facebook.com/reel/123456789/",
        )
        for url in urls:
            with self.subTest(url=url):
                request = api.normalize_request({"post_url": url})
                command = api.build_capture_command(request)
                self.assertEqual(command[command.index("-PostUrl") + 1], url)
                self.assertEqual(command[command.index("-ReactionNativeScrollEnabled") + 1], "1")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_uses_unlimited_defaults_when_limit_is_omitted(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({"post_url": "https://www.facebook.com/example/posts/1"})
        command = api.build_capture_command(request)
        self.assertIn("-OpenReactionList", command)
        self.assertIn("-AutoScreenThenDeep", command)
        self.assertNotIn("-EnableRawNetworkCapture", command)
        self.assertIn("-DisableRawHttpCapture", command)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "60")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "0")
        self.assertEqual(command[command.index("-MaxFlows") + 1], "0")
        self.assertEqual(command[command.index("-DurationSeconds") + 1], "0")
        self.assertEqual(command[command.index("-ScrollCount") + 1], "0")
        self.assertEqual(command[command.index("-ReactionProgressPollIntervalMs") + 1], "500")
        self.assertEqual(command[command.index("-ReactionProgressPollTimeoutMs") + 1], "2200")
        self.assertEqual(command[command.index("-ReactionLiveProgressPollIntervalMs") + 1], "150")
        self.assertEqual(command[command.index("-ReactionLiveProgressPollTimeoutMs") + 1], "1200")
        self.assertEqual(command[command.index("-ReactionSteadyNoGrowthPollTimeoutMs") + 1], "500")
        self.assertEqual(command[command.index("-ReactionLongSwipeStartY") + 1], "1700")
        self.assertEqual(command[command.index("-ReactionLongSwipeEndY") + 1], "250")
        self.assertEqual(command[command.index("-ReactionLongSwipeDurationMs") + 1], "300")
        self.assertEqual(command[command.index("-ReactionPipelineBurstSize") + 1], "2")
        self.assertEqual(command[command.index("-ReactionPipelineSwipeGapMs") + 1], "150")
        self.assertEqual(command[command.index("-ReactionNativeScrollEnabled") + 1], "1")
        self.assertEqual(command[command.index("-ReactionNativeScrollTimeoutMs") + 1], "2500")
        self.assertEqual(command[command.index("-ReactionUiDumpEveryN") + 1], "64")
        self.assertEqual(command[command.index("-ReactionMinCycleMs") + 1], "700")
        self.assertEqual(command[command.index("-ReactionInitialPaginationCooldownSeconds") + 1], "4")
        self.assertEqual(command[command.index("-ReactionNoGrowthUiDumpEveryN") + 1], "1")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_treats_unmarked_legacy_limit_as_unlimited(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = {
            "post_url": "https://www.facebook.com/example/posts/1",
            "post_id": "",
            "collect": ["reactions"],
            "reaction_scroll_limit": 3,
            "reaction_scroll_limit_explicit": False,
        }
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "60")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "0")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch("facebook_capture_api.sys.platform", "win32")
    def test_capture_command_uses_explicit_limit_for_both_powershell_caps(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": 17,
        })
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("-ReactionScrollCount") + 1], "17")
        self.assertEqual(command[command.index("-ReactionAbsoluteCap") + 1], "17")

    @mock.patch("facebook_capture_api.sys.platform", "darwin")
    def test_capture_command_forwards_limit_to_macos_runner(self):
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "reaction_scroll_limit": 11,
        })
        command = api.build_capture_command(request)
        self.assertEqual(command[command.index("--max-scrolls") + 1], "11")

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_skips_raw_pcap_when_not_selected(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = api.normalize_request({
            "post_url": "https://www.facebook.com/example/posts/1",
            "save_artifacts": ["mitm", "safe_capture", "progress"],
        })
        self.assertNotIn("-EnableRawNetworkCapture", api.build_capture_command(request))


class ProgressTests(unittest.TestCase):
    @staticmethod
    def _write_live_progress(path: Path, users: int, pages: int, terminal: bool) -> None:
        path.write_text(
            json.dumps(
                {
                    "record_line_count": users,
                    "families": {
                        "reaction": {
                            "sources": {
                                "post": {
                                    "user_count": users,
                                    "page_count": pages,
                                    "terminal_page_seen": terminal,
                                }
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_progress_snapshot_is_sanitized_and_lists_evidence_layers(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "raw-network.pcap").write_bytes(b"pcap-data")
            (run_dir / "raw-facebook-http.mitm").write_bytes(b"mitm-data")
            (run_dir / "safe-facebook-http-capture.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text("user_id,user_name\n1,Alice\n", encoding="utf-8")
            (run_dir / "facebook-live-progress.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-09-02T04:09:26Z",
                        "record_line_count": 10,
                        "families": {
                            "reaction": {
                                "sources": {
                                    "secret-post-id": {
                                        "query_records": 4,
                                        "user_count": 75,
                                        "page_count": 4,
                                        "terminal_page_seen": True,
                                        "rate_limit_count": 0,
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            snapshot = api.build_progress_snapshot(
                run_dir,
                job_status="completed",
                android={"state": "device", "boot_completed": True, "proxy": "127.0.0.1:7897"},
            )
            encoded = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("secret-post-id", encoded)
            self.assertNotIn("Alice", encoded)
            self.assertEqual(snapshot["metrics"]["reaction_users"], 75)
            self.assertEqual(snapshot["metrics"]["reaction_pages"], 4)
            self.assertTrue(snapshot["metrics"]["terminal"])
            self.assertEqual(snapshot["metrics"]["valid_reactor_users"], 1)
            names = {item["name"] for item in snapshot["artifacts"]}
            self.assertIn("raw-network.pcap", names)
            self.assertIn("raw-facebook-http.mitm", names)
            self.assertIn("facebook-reactor-users.csv", names)
            users = next(item for item in snapshot["artifacts"] if item["name"] == "facebook-reactor-users.csv")
            pcap = next(item for item in snapshot["artifacts"] if item["name"] == "raw-network.pcap")
            self.assertTrue(users["viewable"])
            self.assertFalse(pcap["viewable"])
            self.assertIn("{job_id}", users["url"])

    def test_bounded_sample_progress_marks_pagination_complete_with_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,user_name\n1,Alice\n", encoding="utf-8"
            )
            (run_dir / "analysis" / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            snapshot = api.build_progress_snapshot(
                run_dir,
                job_status="completed",
                request={"save_artifacts": []},
                completion_scope="bounded-sample",
            )
            self.assertEqual(snapshot["completion_scope"], "bounded-sample")
            pagination = next(stage for stage in snapshot["stages"] if stage["id"] == "pagination")
            complete = next(stage for stage in snapshot["stages"] if stage["id"] == "complete")
            self.assertEqual(pagination["state"], "complete")
            self.assertIn("非全量", complete["detail"])

    def test_progress_merges_current_attempt_history_and_root_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "attempt-002"
            previous = root / "attempt-001"
            for attempt in (current, previous):
                (attempt / "analysis").mkdir(parents=True)
            self._write_live_progress(current / "facebook-live-progress.json", 3, 2, False)
            self._write_live_progress(previous / "facebook-live-progress.json", 10, 7, True)
            (current / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu1,12\nu2,13\n", encoding="utf-8"
            )
            (previous / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu2,8\nu3,9\n", encoding="utf-8"
            )
            checkpoint = root / "checkpoint"
            checkpoint.mkdir()
            (checkpoint / "facebook-reactor-users.csv").write_text(
                "user_id,line\nu4,2\n", encoding="utf-8"
            )

            snapshot = api.build_progress_snapshot(
                current,
                "running",
                evidence_dirs=[current, previous, root, checkpoint],
            )

            self.assertEqual(snapshot["metrics"]["reaction_users"], 10)
            self.assertEqual(snapshot["metrics"]["reaction_pages"], 7)
            self.assertEqual(snapshot["metrics"]["valid_reactor_users"], 4)
            self.assertFalse(snapshot["metrics"]["terminal"])

    def test_unselected_artifacts_are_reported_as_not_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "analysis").mkdir()
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text("user_id\n1\n", encoding="utf-8")
            (run_dir / "analysis" / "facebook-sample-patterns.json").write_text("{}", encoding="utf-8")
            snapshot = api.build_progress_snapshot(
                run_dir,
                "completed",
                request={"save_artifacts": []},
            )
            pcap = next(item for item in snapshot["artifacts"] if item["name"] == "raw-network.pcap")
            users = next(item for item in snapshot["artifacts"] if item["name"] == "facebook-reactor-users.csv")
            self.assertEqual(pcap["state"], "not_saved")
            self.assertFalse(pcap["selected"])
            self.assertEqual(users["state"], "ready")
            self.assertTrue(users["required"])

    def test_prune_optional_artifacts_keeps_required_results(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            for relative in api.ARTIFACT_PATHS.values():
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("data", encoding="utf-8")
            api._prune_optional_artifacts(run_dir, {"save_artifacts": ["safe_capture"]})
            self.assertFalse((run_dir / "raw-network.pcap").exists())
            self.assertFalse((run_dir / "raw-facebook-http.mitm").exists())
            self.assertTrue((run_dir / "safe-facebook-http-capture.jsonl").exists())
            self.assertTrue((run_dir / "analysis" / "facebook-reactor-users.csv").exists())
            self.assertTrue((run_dir / "analysis" / "facebook-sample-patterns.json").exists())

    def test_static_asset_path_is_allowlisted(self):
        self.assertEqual(api.resolve_static_asset("/"), api.WEB_DIR / "index.html")
        self.assertEqual(api.resolve_static_asset("/app.js"), api.WEB_DIR / "app.js")
        self.assertIsNone(api.resolve_static_asset("/../facebook_capture_api.py"))

    @mock.patch("facebook_capture_api.shutil.which")
    @mock.patch('facebook_capture_api.sys.platform', 'win32')
    def test_capture_command_accepts_deterministic_output_dir(self, which):
        which.side_effect = lambda name: "powershell" if name == "powershell" else None
        request = {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]}
        run_dir = Path("C:/work/facebook/runs/api-fb_test")
        command = api.build_capture_command(request, run_dir=run_dir)
        self.assertEqual(command[command.index("-OutputDir") + 1], str(run_dir))


class CaptureJobTests(unittest.TestCase):
    def _run_with_fake_process(self, timeout_seconds=None):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api._find_run_dir", return_value=run_dir), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-test",
                    timeout_seconds=timeout_seconds,
                )
            return process


class BatchAggregationTests(unittest.TestCase):
    def test_batch_outputs_preserve_cross_post_users_and_dedupe_same_post_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            post_rows = (
                (
                    ("1", "LIKE"),
                    ("1", "LOVE"),
                    ("2", "LIKE"),
                ),
                (
                    ("2", "LIKE"),
                    ("3", "LIKE"),
                ),
            )
            for index, rows in enumerate(post_rows, start=1):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["user_id", "user_name", "avatar_url", "source_post_id", "reaction_id"])
                    writer.writeheader()
                    for user_id, reaction_id in rows:
                        writer.writerow({"user_id": user_id, "user_name": f"User {user_id}", "avatar_url": "", "source_post_id": f"post-{index}", "reaction_id": reaction_id})
                jobs.append({
                    "job_id": f"fb_{index}",
                    "status": "completed",
                    "run_dir": str(run_dir),
                    "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    "error": None,
                })
            summary = api.write_batch_outputs(root / "batch", "batch_test", jobs)
            self.assertEqual(summary["per_post_valid_sum"], 4)
            self.assertEqual(summary["post_user_records"], 4)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 1)
            self.assertEqual(summary["post_user_record_count"], 4)
            self.assertEqual(summary["same_post_duplicate_occurrences"], 1)
            self.assertEqual(summary["cross_post_unique_users"], 3)
            self.assertEqual(summary["cross_post_duplicate_occurrences"], 1)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            with (root / "batch" / "batch-user-post-relations.csv").open(encoding="utf-8-sig") as handle:
                relations = list(csv.DictReader(handle))
            self.assertEqual(len(users), 4)
            self.assertEqual(len(relations), 4)
            self.assertEqual(
                {(row["post_index"], row["user_id"]) for row in users},
                {("1", "1"), ("1", "2"), ("2", "2"), ("2", "3")},
            )
            self.assertEqual(
                {(row["post_index"], row["user_id"]) for row in relations},
                {("1", "1"), ("1", "2"), ("2", "2"), ("2", "3")},
            )
            summary_again = api.write_batch_outputs(root / "batch", "batch_test", jobs)
            self.assertEqual(summary_again["per_post_valid_sum"], summary["per_post_valid_sum"])
            self.assertEqual(summary_again["post_user_records"], summary["post_user_records"])
            self.assertEqual(summary_again["post_user_record_count"], summary["post_user_record_count"])
            self.assertEqual(summary_again["cross_post_unique_users"], summary["cross_post_unique_users"])
            with (root / "batch" / "batch-user-post-relations.csv").open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 4)

    def test_batch_outputs_keep_cross_post_rows_for_one_global_user(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            for index in (1, 2):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": f"post-{index}",
                            "reaction_id": "LIKE",
                        }
                    )
                jobs.append(
                    {
                        "job_id": f"fb_{index}",
                        "status": "completed",
                        "run_dir": str(run_dir),
                        "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    }
                )

            summary = api.write_batch_outputs(root / "batch", "one_user_batch", jobs)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 1)
            self.assertEqual(len(users), 2)
            self.assertEqual({row["post_index"] for row in users}, {"1", "2"})

    def test_batch_scope_does_not_merge_jobs_with_same_source_post_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = []
            for index in (1, 2):
                run_dir = root / f"post-{index:03d}"
                analysis = run_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": "same-source-id",
                            "reaction_id": "LIKE",
                        }
                    )
                jobs.append(
                    {
                        "job_id": f"fb_{index}",
                        "status": "completed",
                        "run_dir": str(run_dir),
                        "request": {"post_url": f"https://www.facebook.com/example/posts/{index}"},
                    }
                )

            summary = api.write_batch_outputs(root / "batch", "same_source_batch", jobs)
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 2)
            self.assertEqual(summary["cross_post_unique_users"], 1)
            self.assertEqual(len(users), 2)

    def test_batch_scope_merges_missing_source_id_across_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempt_dirs = [root / "attempt-001", root / "attempt-002"]
            for attempt_dir, source_post_id, reaction_id in (
                (attempt_dirs[0], "", "LIKE"),
                (attempt_dirs[1], "source-1", "LOVE"),
            ):
                analysis = attempt_dir / "analysis"
                analysis.mkdir(parents=True)
                with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "user_id": "same-user",
                            "user_name": "Same User",
                            "source_post_id": source_post_id,
                            "reaction_id": reaction_id,
                        }
                    )
            job = {
                "job_id": "fb_attempts",
                "status": "completed",
                "run_dir": str(root / "post-001"),
                "attempt_dirs": [str(path) for path in attempt_dirs],
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
            }

            summary = api.write_batch_outputs(root / "batch", "attempt_batch", [job])
            with (root / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 1)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 1)
            self.assertEqual(len(users), 1)

    def test_batch_outputs_read_root_checkpoint_rows_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "post-001"
            checkpoint = root / "checkpoint"
            checkpoint.mkdir(parents=True)
            with (checkpoint / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "user_id": "checkpoint-user",
                        "user_name": "Checkpoint User",
                        "source_post_id": "checkpoint-post",
                        "reaction_id": "LIKE",
                    }
                )
            job = {
                "job_id": "fb_checkpoint",
                "status": "completed",
                "run_dir": str(root),
                "request": {"post_url": "https://www.facebook.com/example/posts/checkpoint"},
            }

            summary = api.write_batch_outputs(root.parent / "batch", "checkpoint_batch", [job])
            with (root.parent / "batch" / "batch-reactor-users.csv").open(encoding="utf-8-sig") as handle:
                users = list(csv.DictReader(handle))
            with (root.parent / "batch" / "batch-posts.csv").open(encoding="utf-8-sig") as handle:
                posts = list(csv.DictReader(handle))
            self.assertEqual(summary["post_user_records"], 1)
            self.assertEqual(summary["per_post_valid_sum"], 1)
            self.assertEqual(summary["within_post_duplicate_occurrences"], 0)
            self.assertEqual(len(users), 1)
            self.assertEqual(posts[0]["valid_users"], "1")


class CaptureJobBehaviorTests(CaptureJobTests):
    def test_capture_job_has_no_runtime_hard_timeout_by_default(self):
        process = self._run_with_fake_process()
        process.wait.assert_called_once_with()

    def test_capture_job_still_supports_explicit_positive_timeout(self):
        process = self._run_with_fake_process(37)
        process.wait.assert_called_once_with(timeout=37)

    def test_capture_job_registers_and_clears_process_callback(self):
        callbacks = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-callback",
                    process_callback=callbacks.append,
                )
        self.assertIs(callbacks[0], process)
        self.assertIsNone(callbacks[-1])

    def test_capture_job_log_is_scoped_to_attempt_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            attempt = base / "runs" / "api-fb_log" / "attempt-002"
            process = mock.Mock()
            process.returncode = 0
            with mock.patch.object(api, "BASE_DIR", base), \
                 mock.patch("facebook_capture_api.ensure_emulator"), \
                 mock.patch("facebook_capture_api.build_capture_command", return_value=["powershell"]), \
                 mock.patch("facebook_capture_api.subprocess.Popen", return_value=process), \
                 mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}):
                result = api.run_capture_job(
                    {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                    "job-log",
                    run_dir=attempt,
                )

            self.assertEqual(result["job_log"], str(attempt / "capture.log"))
            self.assertTrue((attempt / "capture.log").is_file())


class JobPersistenceTests(unittest.TestCase):
    def _manager(self, base: Path):
        patcher = mock.patch.object(api, "BASE_DIR", base)
        patcher.start()
        self.addCleanup(patcher.stop)
        thread_patcher = mock.patch("facebook_capture_api.threading.Thread.start")
        thread_patcher.start()
        self.addCleanup(thread_patcher.stop)
        return api.JobManager()

    def test_submit_writes_unlimited_default_without_result_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            path = base / "api-jobs" / f"{job['job_id']}.job.json"
            self.assertTrue(path.exists())
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "queued")
            self.assertIsNone(manifest["run_dir"])
            self.assertEqual(
                manifest["request"]["reaction_scroll_limit"],
                api.REACTION_SCROLL_LIMIT_DEFAULT,
            )
            self.assertFalse(manifest["request"]["reaction_scroll_limit_explicit"])
            self.assertIsNone(manifest["completion_scope"])
            self.assertNotIn("result", manifest)

    def test_bounded_completion_scope_is_persisted_and_labeled_in_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch_id = "batch_bounded"
            job_id = "fb_bounded"
            batch_dir = base / "runs" / batch_id
            job = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "post_id": "",
                    "collect": ["reactions"],
                    "save_artifacts": [],
                    "reaction_scroll_limit": 3,
                    "reaction_scroll_limit_explicit": True,
                },
                "result": None,
                "error": None,
                "run_dir": str(batch_dir / "post-001"),
                "completion_scope": "bounded-sample",
            }
            batch = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(batch_dir),
                "completion_scope": "bounded-sample",
            }
            manager._jobs[job_id] = job
            manager._batches[batch_id] = batch
            manager._persist_job(job)
            manager._persist_batch(batch)
            refreshed = manager._refresh_batch_outputs(batch_id)
            progress = manager.batch_progress(batch_id)

            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            batch_manifest = json.loads((base / "api-batches" / f"{batch_id}.batch.json").read_text(encoding="utf-8"))
            self.assertEqual(job_manifest["completion_scope"], "bounded-sample")
            self.assertEqual(batch_manifest["completion_scope"], "bounded-sample")
            self.assertEqual(manager.public_job(job_id)["completion_scope"], "bounded-sample")
            self.assertEqual(refreshed["completion_scope"], "bounded-sample")
            self.assertEqual(progress["completion_scope"], "bounded-sample")
            self.assertEqual(progress["posts"][0]["completion_scope"], "bounded-sample")
            self.assertEqual(progress["posts"][0]["current_stage"], "样本完成（非全量）")

    def test_batch_progress_recomputes_post_user_counts_from_live_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch_id = "batch_live_counts"
            job_id = "fb_live_counts"
            batch_dir = base / "runs" / batch_id
            run_dir = batch_dir / "post-001"
            job = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "completed",
                "created_at": api.utc_now(),
                "request": {"post_url": "https://www.facebook.com/example/posts/1"},
                "run_dir": str(run_dir),
            }
            batch = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(batch_dir),
            }
            manager._jobs[job_id] = job
            manager._batches[batch_id] = batch

            api.write_batch_outputs(batch_dir, batch_id, [job])
            analysis = run_dir / "analysis"
            analysis.mkdir(parents=True)
            with (analysis / "facebook-reactor-users.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["user_id", "user_name", "source_post_id", "reaction_id"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"user_id": "1", "user_name": "User 1", "source_post_id": "post-1", "reaction_id": "LIKE"},
                        {"user_id": "1", "user_name": "User 1", "source_post_id": "post-1", "reaction_id": "LOVE"},
                    ]
                )

            progress = manager.batch_progress(batch_id)

            self.assertEqual(progress["post_user_record_count"], 1)
            self.assertEqual(progress["same_post_duplicate_occurrences"], 1)
            persisted = json.loads((batch_dir / "batch-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["post_user_record_count"], 0)

    def test_submit_batch_persists_ordered_children_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": [
                    "https://www.facebook.com/example/posts/1",
                    "https://www.facebook.com/example/posts/2",
                ],
                "save_artifacts": [],
                "reaction_scroll_limit": 17,
            })
            self.assertEqual(batch["total_posts"], 2)
            self.assertEqual([post["index"] for post in batch["posts"]], [1, 2])
            manifest_path = base / "api-batches" / f"{batch['batch_id']}.batch.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["job_ids"]), 2)
            for index, job_id in enumerate(manifest["job_ids"], start=1):
                job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
                self.assertEqual(job_manifest["batch_id"], batch["batch_id"])
                self.assertEqual(job_manifest["batch_index"], index)
                self.assertEqual(job_manifest["request"]["reaction_scroll_limit"], 17)
                self.assertTrue(job_manifest["request"]["reaction_scroll_limit_explicit"])
                self.assertIn(f"post-{index:03d}", job_manifest["run_dir"])

    def test_submit_batch_preserves_unlimited_default_as_not_explicit_on_children(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": ["https://www.facebook.com/example/posts/1"],
                "save_artifacts": [],
            })
            job_id = batch["posts"][0]["job_id"]
            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertEqual(
                job_manifest["request"]["reaction_scroll_limit"],
                api.REACTION_SCROLL_LIMIT_DEFAULT,
            )
            self.assertFalse(job_manifest["request"]["reaction_scroll_limit_explicit"])

    def test_submit_batch_explicit_child_request_accepts_bounded_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            batch = manager.submit_batch({
                "post_urls": ["https://www.facebook.com/example/posts/1"],
                "save_artifacts": [],
                "reaction_scroll_limit": 3,
            })
            job_id = batch["posts"][0]["job_id"]
            job = manager._jobs[job_id]
            job_manifest = json.loads((base / "api-jobs" / f"{job_id}.job.json").read_text(encoding="utf-8"))
            self.assertTrue(job_manifest["request"]["reaction_scroll_limit_explicit"])

            run_dir = Path(job["run_dir"])
            ResultTests._write_reaction_fixture(run_dir)
            result = api.build_api_result(run_dir, job["request"])
            self.assertEqual(result["completion_scope"], api.BOUNDED_SAMPLE_COMPLETION_SCOPE)

    def test_failed_rate_limited_batch_post_is_labeled_as_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            run_dir = base / "runs" / "batch_rate_limited" / "post-001"
            diagnostics = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics.parent.mkdir(parents=True)
            diagnostics.write_text(
                json.dumps({"pagination_runs": [{"stop_reason": "rate-limited", "attempts": 12}]}),
                encoding="utf-8",
            )
            (run_dir / "facebook-live-progress.json").write_text(
                json.dumps({
                    "families": {"reaction": {"sources": {"post-1": {"user_count": 25, "page_count": 5}}}}
                }),
                encoding="utf-8",
            )
            job_id = "fb_rate_limited"
            batch_id = "batch_rate_limited"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "batch_index": 1,
                "status": "failed",
                "request": {"post_url": "https://www.facebook.com/share/r/generic/"},
                "run_dir": str(run_dir),
                "error": "reaction capture is incomplete: rate-limited",
            }
            manager._batches[batch_id] = {
                "batch_id": batch_id,
                "created_at": api.utc_now(),
                "job_ids": [job_id],
                "run_dir": str(run_dir.parent),
            }
            progress = manager.batch_progress(batch_id)
            self.assertEqual(progress["posts"][0]["current_stage"], "限流，已保留部分结果")

    def test_queued_job_can_be_cancelled_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manager = self._manager(base)
            job = manager.submit({"post_url": "https://www.facebook.com/example/posts/1"})
            response = manager.cancel(job["job_id"])
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelled")
            path = base / "api-jobs" / f"{job['job_id']}.job.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "cancelled")

    def test_running_job_cancel_terminates_registered_process(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_runningcancel"
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "result": None,
                "error": None,
                "run_dir": None,
            }
            process = mock.Mock()
            manager._running_processes[job_id] = process
            with mock.patch("facebook_capture_api._terminate_tree") as terminate:
                response = manager.cancel(job_id)
            terminate.assert_called_once_with(process)
            self.assertTrue(response["cancelled"])
            self.assertEqual(response["job"]["status"], "cancelled")

    def test_repeat_cancel_does_not_change_completed_job_or_result(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            job_id = "fb_donecancel"
            result = {"ok": True}
            manager._jobs[job_id] = {
                "job_id": job_id,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "result": result,
                "error": None,
                "run_dir": None,
            }
            response = manager.cancel(job_id)
            self.assertFalse(response["cancelled"])
            self.assertIn("already completed", response["reason"])
            self.assertEqual(manager._jobs[job_id]["status"], "completed")
            self.assertIs(manager._jobs[job_id]["result"], result)

    def test_cancelled_job_remains_cancelled_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_persistedcancelled"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "cancelled",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": None,
                "finished_at": "2026-08-18T00:00:02Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            self.assertEqual(manager.public_job(job_id)["status"], "cancelled")

    def test_queued_job_is_requeued_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_persistedqueued"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "queued",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": None,
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": None,
                "serial": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "queued")
            self.assertEqual(restored["recovery_state"], "queued-after-restart")
            self.assertIsNone(restored["worker_id"])
            self.assertIsNone(restored["serial"])
            self.assertEqual(manager._queue.get_nowait(), job_id)
            self.assertTrue(manager._queue.empty())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "queued")

    def test_stopped_running_job_is_safely_requeued_after_api_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_restartstopped"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
                "worker_id": "worker-01",
                "serial": "emulator-5554",
                "process_pid": None,
                "process_creation_time": None,
                "process_state": "stopped",
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "queued")
            self.assertEqual(restored["recovery_state"], "requeued-after-restart")
            self.assertIn("safely requeued", restored["error"])
            self.assertIsNone(restored["started_at"])
            self.assertIsNone(restored["worker_id"])
            self.assertIsNone(restored["serial"])
            self.assertEqual(restored["last_worker_id"], "worker-01")
            self.assertEqual(restored["last_serial"], "emulator-5554")
            self.assertEqual(restored["process_state"], "stopped")
            self.assertEqual(manager._queue.get_nowait(), job_id)
            self.assertTrue(manager._queue.empty())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "queued")
            self.assertEqual(saved["recovery_state"], "requeued-after-restart")

            assigned = manager._assign_job_to_worker(job_id, manager.workers["worker-01"])
            self.assertTrue(assigned)
            running = manager.public_job(job_id)
            self.assertEqual(running["status"], "running")
            self.assertIsNone(running["error"])
            self.assertEqual(running["recovery_state"], "running")

    def test_legacy_job_limit_materialization_restores_as_unlimited(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_legacyboundeddefault"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "completed",
                "created_at": api.utc_now(),
                "started_at": api.utc_now(),
                "finished_at": api.utc_now(),
                "request": {
                    "post_url": "https://www.facebook.com/example/posts/1",
                    "post_id": "",
                    "collect": ["reactions"],
                    # This was a materialized default before explicitness was persisted.
                    "reaction_scroll_limit": 3,
                },
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")

            manager = self._manager(base)

            restored_request = manager._jobs[job_id]["request"]
            self.assertIsNone(restored_request["reaction_scroll_limit"])
            self.assertFalse(restored_request["reaction_scroll_limit_explicit"])

    def test_completed_job_is_restored_and_result_is_built_lazily(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "done"
            run_dir.mkdir(parents=True)
            job_id = "fb_persistedcompleted"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "completed",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": "2026-08-18T00:01:00Z",
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(run_dir),
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            with mock.patch("facebook_capture_api.build_api_result", return_value={"ok": True}) as build:
                self.assertEqual(manager.public_job(job_id)["status"], "completed")
                self.assertEqual(manager.result(job_id), {"ok": True})
                self.assertEqual(manager.result(job_id), {"ok": True})
                build.assert_called_once()

    def test_running_job_becomes_failed_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            job_id = "fb_interruptedjob"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": None,
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "failed")
            self.assertIn("interrupted by API service restart", restored["error"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")

    def test_failed_restarted_job_keeps_partial_evidence_location(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            run_dir = base / "runs" / "api-fb_interruptedpartial"
            run_dir.mkdir(parents=True)
            (run_dir / "safe-facebook-http-capture.jsonl").write_text('{"safe":true}\n', encoding="utf-8")
            job_id = "fb_interruptedpartial"
            manifest = {
                "version": 1,
                "job_id": job_id,
                "status": "running",
                "created_at": "2026-08-18T00:00:00Z",
                "started_at": "2026-08-18T00:00:01Z",
                "finished_at": None,
                "request": {"post_url": "https://www.facebook.com/example/posts/1", "post_id": "", "collect": ["reactions"]},
                "error": None,
                "run_dir": str(run_dir),
            }
            path = base / "api-jobs" / f"{job_id}.job.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            manager = self._manager(base)
            restored = manager.public_job(job_id)
            self.assertEqual(restored["status"], "failed")
            self.assertEqual(restored["run_dir"], str(run_dir))
            self.assertTrue(restored["partial_evidence_available"])

    def test_malformed_manifest_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            path = base / "api-jobs" / "fb_bad.job.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not-json", encoding="utf-8")
            manager = self._manager(base)
            with self.assertRaises(KeyError):
                manager.public_job("fb_bad")

    def test_latest_job_returns_most_recent_created_job(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self._manager(Path(temp))
            manager._jobs = {
                "fb_old": {"job_id": "fb_old", "status": "completed", "created_at": "2026-01-01T00:00:00Z", "run_dir": None},
                "fb_new": {"job_id": "fb_new", "status": "completed", "created_at": "2026-02-01T00:00:00Z", "run_dir": None},
            }
            self.assertEqual(manager.latest_job()["job_id"], "fb_new")


class HttpCancelTests(unittest.TestCase):
    def test_cancel_endpoint_routes_without_request_body(self):
        class FakeManager:
            def __init__(self):
                self.job_id = None

            def cancel(self, job_id):
                self.job_id = job_id
                return {"cancelled": True, "reason": "queued job cancelled", "job": {"job_id": job_id, "status": "cancelled"}}

        manager = FakeManager()
        server = api.CaptureServer(("127.0.0.1", 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/jobs/fb_httpcancel/cancel")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["cancelled"])
            self.assertEqual(manager.job_id, "fb_httpcancel")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpWorkerReloadTests(unittest.TestCase):
    def test_reload_workers_endpoint_routes_without_request_body(self):
        class FakeManager:
            def __init__(self):
                self.called = False

            def reload_workers(self):
                self.called = True
                return {"added": ["worker-03"], "updated": [], "worker_ids": ["worker-01", "worker-03"]}

        manager = FakeManager()
        server = api.CaptureServer(("127.0.0.1", 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/workers/reload")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(manager.called)
            self.assertEqual(payload["added"], ["worker-03"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpUiTests(unittest.TestCase):
    def test_ui_assets_and_progress_endpoint_are_served(self):
        class FakeManager:
            def health(self):
                return {"service": "ready", "worker": "idle", "android": {"state": "device"}, "serial": "emulator-5554"}

            def progress(self, job_id):
                return {"job_status": "running", "storage_root": "C:/runs/test", "metrics": {}, "stages": [], "artifacts": []}

        server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            for path, expected_type in (("/", "text/html"), ("/styles.css", "text/css"), ("/app.js", "javascript")):
                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read()
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn(expected_type, response.getheader("Content-Type"))
                self.assertGreater(len(body), 20)

            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/v1/jobs/fb_ui/progress")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["job_status"], "running")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class HttpBatchTests(unittest.TestCase):
    def test_batch_create_latest_progress_and_cancel_routes(self):
        class FakeManager:
            def submit_batch(self, payload):
                return {"batch_id": "batch_http", "status": "queued", "total_posts": len(payload["post_urls"]), "posts": []}

            def latest_batch(self):
                return {"batch_id": "batch_http", "status": "running", "total_posts": 2, "posts": []}

            def batch_progress(self, batch_id):
                return {"batch_id": batch_id, "status": "running", "total_posts": 2, "posts": []}

            def cancel_batch(self, batch_id):
                return {"cancelled_jobs": 2, "batch": {"batch_id": batch_id}}

        server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            body = json.dumps({"post_urls": ["https://www.facebook.com/example/posts/1"]})
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/batches", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            created = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 202)
            self.assertEqual(created["batch_id"], "batch_http")

            for path in ("/v1/batches/latest", "/v1/batches/batch_http/progress"):
                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", path)
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["batch_id"], "batch_http")

            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/batches/batch_http/cancel")
            response = connection.getresponse()
            cancelled = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(cancelled["cancelled_jobs"], 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_artifact_can_be_viewed_downloaded_and_unknown_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            result_file = run_dir / "analysis" / "facebook-reactor-users.csv"
            result_file.parent.mkdir()
            result_file.write_text("user_id,user_name\n1,Alice\n", encoding="utf-8")

            class FakeManager:
                def artifact(self, job_id, artifact_name):
                    if artifact_name != "facebook-reactor-users.csv":
                        raise api.ApiError("unknown artifact")
                    return result_file

            server = api.CaptureServer(("127.0.0.1", 0), FakeManager())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                for suffix, disposition in (("", "inline"), ("?download=1", "attachment")):
                    connection = http.client.HTTPConnection(host, port, timeout=3)
                    connection.request("GET", f"/v1/jobs/fb_ui/artifacts/facebook-reactor-users.csv{suffix}")
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertIn("text/csv", response.getheader("Content-Type"))
                    self.assertTrue(response.getheader("Content-Disposition").startswith(disposition))
                    self.assertIn("Alice", body)

                connection = http.client.HTTPConnection(host, port, timeout=3)
                connection.request("GET", "/v1/jobs/fb_ui/artifacts/not-allowed.txt")
                response = connection.getresponse()
                response.read()
                connection.close()
                self.assertEqual(response.status, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class ResultTests(unittest.TestCase):
    @staticmethod
    def _write_reaction_fixture(
        run_dir: Path,
        *,
        users: int = 2,
        stop_reason: str = "absolute-cap",
        target_status: str = "matched",
        source_post_ids: list[str] | None = None,
        rate_limited_records: int = 0,
        temporary_feature_block_records: int = 0,
        line_parse_errors: int = 0,
        row_source_post_id: str = "p1",
    ) -> None:
        analysis = run_dir / "analysis"
        diagnostics = run_dir / "readonly-ui"
        analysis.mkdir(parents=True)
        diagnostics.mkdir(parents=True)
        source_post_ids = source_post_ids if source_post_ids is not None else ["p1"]
        report = {
            "line_parse_errors": line_parse_errors,
            "target_verification": {"status": target_status, "matched_post_id": "p1"},
            "source_post_ids_by_family": {"reactions": source_post_ids, "shares": []},
            "statuses": {"200": 8},
            "reaction_integrity": {
                "integrity_status": "partial-open-tail",
                "query_records": 1,
                "successful_page_count": 1,
                "raw_reactor_rows": users,
                "unique_reactor_identities": users,
                "rate_limited_records": rate_limited_records,
                "temporary_feature_block_records": temporary_feature_block_records,
                "terminal_page_seen": False,
                "open_tail_without_terminal": True,
                "reported_total_count": users + 10,
                "completion_claim_allowed": False,
            },
            "enumeration_status": {
                "reactions": {
                    "query_records": 1,
                    "reported_total_count": users + 10,
                    "enumerated_identity_count": users,
                    "pagination": {
                        "groups": {"__ALL__": {"open_page_seen": True, "terminal_page_seen": False}}
                    },
                }
            },
        }
        (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
        with (analysis / "facebook-reactor-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(api.REACTOR_USER_FIELDS))
            writer.writeheader()
            for index in range(users):
                writer.writerow(
                    {
                        "user_id": f"u{index + 1}",
                        "user_name": f"User {index + 1}",
                        "avatar_url": "",
                        "source_post_id": row_source_post_id,
                        "reaction_id": "LIKE",
                    }
                )
        (run_dir / "readonly-ui" / "exploration-diagnostics.json").write_text(
            json.dumps(
                {
                    "pagination_runs": [
                        {
                            "reaction_key": "__ALL__",
                            "stop_reason": stop_reason,
                            "attempts": 3,
                            "absolute_cap": 3,
                            "final_budget": 3,
                            "terminal_seen": False,
                            "open_seen": True,
                            "latest_graphql_error_code": None,
                        }
                    ],
                    "reaction_screening": {
                        "latest_graphql_error_code": None,
                        "session_restricted": False,
                    },
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_group_url_token_fixture(
        run_dir: Path,
        *,
        token: str = "2158908574674305",
        source_post_id: str | None = None,
        users: int = 2,
    ) -> None:
        source_post_id = source_post_id or token
        ResultTests._write_reaction_fixture(
            run_dir,
            users=users,
            source_post_ids=[source_post_id],
            row_source_post_id=source_post_id,
        )
        report_path = run_dir / "analysis" / "facebook-sample-patterns.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["target_verification"] = {
            "status": "matched",
            "matched_by": "graphql-response-url-token",
            "expected_url": (
                "https://www.facebook.com/groups/1020765891821918/posts/"
                f"{token}/"
            ),
            "expected_url_tokens": [token],
            "matched_token": token,
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

    @staticmethod
    def _write_empty_reaction_visibility_fixture(
        run_dir: Path,
        *,
        status: str,
        query_records: int,
        source_post_ids: list[str],
        reported_total_count: int,
        visibility_hint: str | None = None,
        terminal_page_seen: bool = False,
        diagnostics: dict | None = None,
    ) -> None:
        analysis = run_dir / "analysis"
        analysis.mkdir(parents=True)
        readonly_ui = run_dir / "readonly-ui"
        readonly_ui.mkdir(parents=True)
        report = {
            "line_parse_errors": 0,
            "target_verification": {"status": "matched", "matched_post_id": "p1"},
            "source_post_ids_by_family": {"reactions": source_post_ids, "shares": []},
            "statuses": {"200": 4},
            "reaction_integrity": {
                "integrity_status": status,
                "query_records": query_records,
                "successful_page_count": query_records,
                "raw_reactor_rows": 0,
                "unique_reactor_identities": 0,
                "rate_limited_records": 0,
                "temporary_feature_block_records": 0,
                "terminal_page_seen": terminal_page_seen,
                "open_tail_without_terminal": False,
                "reported_total_count": reported_total_count,
                "reported_total_unique_gap": reported_total_count,
                "completion_claim_allowed": terminal_page_seen,
            },
            "enumeration_status": {
                "reactions": {
                    "query_records": query_records,
                    "status": status,
                    "ui_visibility_hint": visibility_hint,
                    "reported_total_count": reported_total_count,
                    "reported_total_source": "target-post-feedback",
                    "enumerated_identity_count": 0,
                    "identity_count_gap": reported_total_count,
                    "terminal_page_seen": terminal_page_seen,
                    "pagination": {
                        "groups": {
                            "__ALL__": {
                                "open_page_seen": False,
                                "terminal_page_seen": terminal_page_seen,
                            }
                        },
                        "all_observed_groups_terminal": terminal_page_seen,
                    },
                }
            },
        }
        (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
        (analysis / "facebook-reactor-users.csv").write_text(
            "user_id,user_name,source_post_id\n",
            encoding="utf-8",
        )
        default_diagnostics = {
            "pagination_runs": (
                []
                if query_records == 0
                else [{
                    "reaction_key": "__ALL__",
                    "terminal_seen": terminal_page_seen,
                    "parse_errors": 0,
                    "latest_rate_limited": False,
                    "session_restricted": False,
                    "latest_graphql_error_code": None,
                    "latest_api_error_code": None,
                }]
            ),
            "reaction_screening": (
                None
                if query_records == 0
                else {
                    "parse_errors": 0,
                    "latest_rate_limited": False,
                    "session_restricted": False,
                    "latest_graphql_error_code": None,
                    "latest_api_error_code": None,
                }
            ),
        }
        (readonly_ui / "exploration-diagnostics.json").write_text(
            json.dumps(default_diagnostics if diagnostics is None else diagnostics),
            encoding="utf-8",
        )

    def test_build_result_accepts_url_token_group_target_from_attempt_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            attempt = root / "attempt-001"
            self._write_group_url_token_fixture(attempt, users=2)
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            result = api.build_api_result(root, request, evidence_dirs=[attempt])

            self.assertEqual(result["completion_scope"], "bounded-sample")
            self.assertEqual(
                {row["source_post_id"] for row in result["reaction_users"]},
                {"2158908574674305"},
            )

    def test_build_result_rejects_url_token_source_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            attempt = root / "attempt-001"
            self._write_group_url_token_fixture(
                attempt,
                source_post_id="different-source",
            )
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            with self.assertRaises(api.ApiError):
                api.build_api_result(root, request, evidence_dirs=[attempt])

    def test_build_result_rejects_required_artifacts_split_across_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "job-root"
            first = root / "attempt-001"
            second = root / "attempt-002"
            self._write_group_url_token_fixture(first)
            self._write_group_url_token_fixture(second)
            (first / "analysis" / "facebook-reactor-users.csv").unlink()
            (second / "analysis" / "facebook-sample-patterns.json").unlink()
            request = api.normalize_request({
                "post_url": "https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/",
                "reaction_scroll_limit": 3,
            })

            with self.assertRaises(api.ApiError):
                api.build_api_result(root, request, evidence_dirs=[first, second])

    def test_build_result_accepts_explicit_bounded_sample_without_claiming_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 3,
            })
            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], "bounded-sample")
            completeness = result["completeness"]["reactions"]
            self.assertFalse(completeness["completion_claim_allowed"])
            self.assertFalse(completeness["terminal_page_seen"])
            self.assertEqual(completeness["integrity_status"], "partial-open-tail")

    def test_build_result_accepts_terminal_visibility_restricted_reaction_list_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )

            result = api.build_api_result(
                run_dir,
                {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
            )

            self.assertEqual(result["completion_scope"], api.TERMINAL_COMPLETION_SCOPE)
            self.assertEqual(result["reaction_users"], [])
            self.assertEqual(result["visibility"]["reactions"]["status"], "query-triggered-friends-only-visibility")

    def test_build_result_accepts_bounded_visibility_restricted_reaction_list_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=1,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=False,
            )
            diagnostics_path = run_dir / "readonly-ui" / "exploration-diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            diagnostics["pagination_runs"][0].update(
                {"stop_reason": "absolute-cap", "absolute_cap": 1, "final_budget": 1}
            )
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")

            request = api.normalize_request({
                "post_url": "https://facebook.com/x",
                "reaction_scroll_limit": 1,
            })
            result = api.build_api_result(run_dir, request)

            self.assertEqual(result["completion_scope"], api.BOUNDED_SAMPLE_COMPLETION_SCOPE)
            self.assertEqual(result["reaction_users"], [])
            self.assertEqual(result["visibility"]["reactions"]["status"], "query-triggered-friends-only-visibility")

    def test_build_result_rejects_untriggered_reaction_list_when_target_feedback_reports_reactions(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-not-triggered",
                query_records=0,
                source_post_ids=[],
                reported_total_count=1162,
            )

            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_visible_rows_without_verified_reaction_source(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=[],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )
            (run_dir / "analysis" / "facebook-reactor-users.csv").write_text(
                "user_id,user_name,source_post_id\nu1,User 1,p1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(api.ApiError, "reaction capture source mismatch"):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_empty_visibility_with_diagnostic_errors(self):
        cases = (
            {"pagination_runs": [{"parse_errors": 1}]},
            {"pagination_runs": [{"latest_graphql_error_code": 1675004}]},
            {"pagination_runs": [{"latest_api_error_code": 368}]},
            {"reaction_screening": {"parse_errors": 1}},
            {"reaction_screening": {"session_restricted": True}},
            {"reaction_screening": {"session_restricted": 1}},
            {"pagination_runs": [{"session_restricted": "true"}]},
            {"reaction_screening": {"latest_graphql_error_code": 1675004}},
            {"reaction_screening": {"latest_api_error_code": 368}},
        )
        for diagnostics in cases:
            with self.subTest(diagnostics=diagnostics), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_empty_reaction_visibility_fixture(
                    run_dir,
                    status="query-triggered-friends-only-visibility",
                    query_records=2,
                    source_post_ids=["p1"],
                    reported_total_count=4494,
                    visibility_hint="friends-only-names",
                    terminal_page_seen=True,
                    diagnostics=diagnostics,
                )

                with self.assertRaises(api.ApiError):
                    api.build_api_result(
                        run_dir,
                        {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                    )

    def test_build_result_rejects_empty_visibility_with_fallback_diagnostic_error(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_empty_reaction_visibility_fixture(
                run_dir,
                status="query-triggered-friends-only-visibility",
                query_records=2,
                source_post_ids=["p1"],
                reported_total_count=4494,
                visibility_hint="friends-only-names",
                terminal_page_seen=True,
            )
            fallback = run_dir / "readonly-ui-fallback"
            fallback.mkdir()
            (fallback / "exploration-diagnostics.json").write_text(
                json.dumps({"reaction_screening": {"session_restricted": True}, "pagination_runs": []}),
                encoding="utf-8",
            )

            with self.assertRaises(api.ApiError):
                api.build_api_result(
                    run_dir,
                    {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]},
                )

    def test_build_result_rejects_default_request_for_bounded_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            self._write_reaction_fixture(run_dir)
            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(
                    run_dir,
                    api.normalize_request({"post_url": "https://facebook.com/x"}),
                )

    def test_bounded_sample_requires_positive_users_and_clean_transport(self):
        cases = (
            {"users": 0},
            {"rate_limited_records": 1},
            {"temporary_feature_block_records": 1},
            {"line_parse_errors": 1},
        )
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_reaction_fixture(run_dir, **options)
                request = api.normalize_request({
                    "post_url": "https://facebook.com/x",
                    "reaction_scroll_limit": 3,
                })
                with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                    api.build_api_result(run_dir, request)

    def test_bounded_sample_requires_matched_source_and_absolute_cap(self):
        cases = (
            {"target_status": "mismatch"},
            {"source_post_ids": ["other"]},
            {"stop_reason": "open-no-recent-growth"},
        )
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp)
                self._write_reaction_fixture(run_dir, **options)
                request = api.normalize_request({
                    "post_url": "https://facebook.com/x",
                    "reaction_scroll_limit": 3,
                })
                with self.assertRaises(api.ApiError):
                    api.build_api_result(run_dir, request)

    def test_build_result_returns_users_avatar_and_visibility(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {
                "target_verification": {"status": "matched", "matched_by": "post-id", "matched_post_id": "p1"},
                "source_post_ids_by_family": {"reactions": ["p1"], "shares": []},
                "statuses": {"200": 10},
                "reaction_integrity": {
                    "integrity_status": "complete-terminal",
                    "completion_claim_allowed": True,
                    "terminal_page_seen": True,
                    "open_tail_without_terminal": False,
                    "successful_page_count": 2,
                    "raw_reactor_rows": 2,
                    "unique_reactor_identities": 2,
                    "duplicate_reactor_rows": 0,
                    "reported_total_count": 2,
                    "reported_total_unique_gap": 0,
                    "rate_limited_records": 0,
                    "temporary_feature_block_records": 0,
                },
                "enumeration_status": {
                    "reactions": {
                        "status": "visible-users-enumerated",
                        "ui_total_count": 2,
                        "reported_total_count": 2,
                        "reported_total_source": "ui-entry-label",
                        "enumerated_identity_count": 2,
                        "identity_count_gap": 0,
                        "pagination": {"all_observed_groups_terminal": True, "open_groups": [], "terminal_groups": ["__ALL__"]},
                    },
                    "shares": {"status": "query-not-triggered", "enumerated_identity_count": 0},
                },
            }
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with (analysis / "facebook-reactor-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["user_id", "user_name", "avatar_url", "reaction_id", "source_field", "source_post_id"])
                writer.writeheader()
                writer.writerow({"user_id": "1", "user_name": "Alice", "avatar_url": "https://cdn.example/a.jpg", "reaction_id": "r1", "source_field": "reactors", "source_post_id": "p1"})
                writer.writerow({"user_id": "2", "user_name": "Bob", "avatar_url": "", "reaction_id": "r1", "source_field": "reactors", "source_post_id": "p1"})
            with (analysis / "facebook-share-users.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["actor_id", "actor_name", "source_post_id", "share_story_id"])
                writer.writeheader()
            result = api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})
            self.assertEqual(len(result["reaction_users"]), 2)
            self.assertEqual(result["reaction_users"][0]["avatar_url"], "https://cdn.example/a.jpg")
            self.assertEqual(result["reaction_users"][0]["avatar_status"], "source-only")
            self.assertEqual(result["reaction_users"][0]["avatar_proxy_url"], "/v1/avatars/1")
            self.assertEqual(result["reaction_users"][1]["avatar_status"], "unavailable")
            self.assertEqual(result["reaction_users"][1]["avatar_proxy_url"], "/v1/avatars/2")
            self.assertEqual(result["visibility"]["reactions"]["identity_gap"], 0)
            self.assertEqual(result["visibility"]["reactions"]["reported_total"], 2)
            self.assertFalse(result["visibility"]["reactions"]["limited"])
            self.assertFalse(result["visibility"]["reactions"]["count_difference_observed"])
            self.assertTrue(result["visibility"]["reactions"]["terminal"])
            self.assertTrue(result["completeness"]["reactions"]["completion_claim_allowed"])
            self.assertEqual(result["completeness"]["reactions"]["integrity_status"], "complete-terminal")
            self.assertEqual(result["completion_scope"], "terminal")
            self.assertFalse(result["transport_observation"]["rate_limit_http_seen"])

    def test_build_result_rejects_false_completed_reaction_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {
                "target_verification": {"status": "matched", "matched_post_id": "p1"},
                "source_post_ids_by_family": {"reactions": [], "shares": []},
                "reaction_integrity": {
                    "integrity_status": "query-not-triggered",
                    "query_records": 0,
                    "completion_claim_allowed": False,
                    "terminal_page_seen": False,
                },
                "enumeration_status": {"reactions": {"query_records": 0}, "shares": {}},
            }
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            (analysis / "facebook-reactor-users.csv").write_text("user_id,user_name,source_post_id\n", encoding="utf-8")
            (analysis / "facebook-share-users.csv").write_text("actor_id,actor_name,source_post_id\n", encoding="utf-8")

            with self.assertRaisesRegex(api.ApiError, "reaction capture is incomplete"):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})

    def test_resolve_avatar_url_requires_server_token(self):
        with mock.patch.dict(api.os.environ, {}, clear=True):
            with self.assertRaises(api.ApiError):
                api.resolve_avatar_url("1")

    def test_resolve_avatar_url_returns_non_silhouette_signed_url(self):
        payload = {"data": {"url": "https://scontent.example/avatar.jpg?oh=abc&oe=123", "is_silhouette": False}}
        response = mock.Mock()
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode("utf-8")
        with mock.patch.dict(api.os.environ, {"FACEBOOK_GRAPH_ACCESS_TOKEN": "test-token"}, clear=True), \
             mock.patch("facebook_capture_api.urlopen", return_value=response) as fetch:
            self.assertEqual(api.resolve_avatar_url("1"), payload["data"]["url"])
            self.assertIn("access_token=test-token", fetch.call_args.args[0].full_url)

    def test_reaction_completeness_fallback_never_marks_open_tail_complete(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "enumerated_identity_count": 1350,
                    "pagination": {
                        "groups": {"__ALL__": {"open_page_seen": True, "terminal_page_seen": False}}
                    },
                }
            }
        }
        completeness = api._reaction_completeness(report)
        self.assertFalse(completeness["completion_claim_allowed"])
        self.assertTrue(completeness["open_tail_without_terminal"])
        self.assertEqual(completeness["integrity_status"], "partial-open-tail")

    def test_reaction_all_group_terminal_is_terminal_even_if_category_group_remains_open(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "enumerated_identity_count": 3180,
                    "pagination": {
                        "all_observed_groups_terminal": False,
                        "groups": {
                            "__ALL__": {"terminal_page_seen": True, "open_page_seen": True},
                            "1635855486666999": {"terminal_page_seen": False, "open_page_seen": True},
                        },
                    },
                }
            }
        }
        summary = api._enumeration_summary(report, "reactions")
        self.assertTrue(summary["terminal"])

    def test_reaction_terminal_gap_is_reported_as_platform_difference_not_failure(self):
        report = {
            "enumeration_status": {
                "reactions": {
                    "status": "visible-users-enumerated-subset",
                    "ui_total_count": None,
                    "reported_total_count": 3217,
                    "reported_total_source": "target-post-feedback",
                    "enumerated_identity_count": 3216,
                    "identity_count_gap": 1,
                    "pagination": {"groups": {"__ALL__": {"terminal_page_seen": True}}},
                }
            }
        }
        summary = api._enumeration_summary(report, "reactions")
        self.assertTrue(summary["terminal"])
        self.assertEqual(summary["reported_total"], 3217)
        self.assertEqual(summary["enumerated_count"], 3216)
        self.assertEqual(summary["identity_gap"], 1)
        self.assertTrue(summary["limited"])
        self.assertTrue(summary["count_difference_observed"])
        self.assertIn("not a capture failure", summary["count_difference_note"])

    def test_target_mismatch_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {"target_verification": {"status": "mismatch"}, "enumeration_status": {}}
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(api.ApiError):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})

    def test_missing_target_verification_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            analysis = run_dir / "analysis"
            analysis.mkdir()
            report = {"target_verification": {}, "enumeration_status": {}}
            (analysis / "facebook-sample-patterns.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(api.ApiError):
                api.build_api_result(run_dir, {"post_url": "https://facebook.com/x", "post_id": "", "collect": ["reactions"]})


if __name__ == "__main__":
    unittest.main()
