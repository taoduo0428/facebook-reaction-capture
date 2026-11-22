"""Phase F: native probes use only Python processes created by these tests."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from facebook_backend.platform import process_manager as pm


class ProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def spawn(self, code="import time; time.sleep(15)", *, new_session=True):
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code], cwd=self.root,
            start_new_session=new_session and os.name == "posix",
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        def reap():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

        self.addCleanup(reap)
        return proc

    def wait_file(self, name):
        path = self.root / name
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail(f"test subprocess did not publish {name}")
            time.sleep(0.01)
        return path

    def test_native_birth_is_stable_and_round_trips_legacy_unix_seconds(self):
        before = time.time()
        proc = self.spawn()
        birth = pm.creation_time(proc.pid)
        self.assertIsNotNone(birth)
        self.assertLess(abs(birth - before), 5)
        self.assertEqual(pm.creation_time(proc.pid), birth)
        self.assertEqual(pm.identity_state(proc.pid, json.loads(json.dumps(birth))), "match")
        self.assertTrue(pm.presence(proc.pid))
        self.assertEqual(pm.identity_state(proc.pid, birth + 0.00001), "mismatch")
        self.assertFalse(pm.terminate_known_pid(proc.pid, birth + 0.1))
        self.assertIsNone(proc.poll())
        proc.terminate()
        proc.wait(timeout=5)
        self.assertEqual(pm.identity_state(proc.pid, birth), "absent")

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin's kernel timeval")
    def test_macos_native_birth_preserves_subseconds(self):
        for _ in range(3):
            birth = pm.creation_time(self.spawn().pid)
            self.assertIsNotNone(birth)
            if not birth.is_integer():
                return
        self.fail("all Darwin birth times were rounded to whole seconds")

    def test_invalid_identity_does_not_probe_or_signal(self):
        for pid in (None, 0, -2, True, 1.5, 2**48, "not-a-pid"):
            with self.subTest(pid=pid):
                probe = mock.Mock(side_effect=AssertionError("invalid PID was probed"))
                self.assertIsNone(pm.presence(pid))
                self.assertEqual(pm.identity_state(pid, 123, creation_time_probe=probe), "unknown")
                self.assertFalse(pm.terminate_known_pid(pid, 123, identity_probe=probe))
        for invalid in (None, math.nan, math.inf, -1, True, "unreadable"):
            self.assertEqual(pm.identity_state(123, invalid, creation_time_probe=lambda _: 100), "unknown")
            self.assertEqual(pm.identity_state(123, 100, creation_time_probe=lambda _: invalid,
                                               presence_probe=lambda _: True), "unknown")

    def test_denied_or_unreliable_probe_is_unknown(self):
        for error in (PermissionError(), OSError()):
            self.assertEqual(pm.identity_state(123, 12, creation_time_probe=mock.Mock(side_effect=error),
                                               presence_probe=lambda _: False), "unknown")
            self.assertEqual(pm.identity_state(123, 12, creation_time_probe=lambda _: None,
                                               presence_probe=mock.Mock(side_effect=error)), "unknown")
        with mock.patch.object(pm.os, "name", "posix"), mock.patch.object(pm.os, "kill", side_effect=PermissionError()):
            self.assertIsNone(pm.presence(123))

    def test_changed_generation_immediately_before_posix_group_signal_is_protected(self):
        probe = mock.Mock(side_effect=["match", "mismatch"])
        with mock.patch.object(pm.os, "name", "posix"), \
             mock.patch.object(pm.os, "getpgid", return_value=123, create=True), \
             mock.patch.object(pm.os, "getsid", return_value=123, create=True), \
             mock.patch.object(pm.os, "killpg", create=True) as signal_group:
            self.assertFalse(pm.terminate_known_pid(123, 12, identity_probe=probe))
        signal_group.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "requires Windows process handles")
    def test_windows_rechecks_handle_even_if_initial_match_went_stale(self):
        proc = self.spawn()
        birth = pm.creation_time(proc.pid)
        with mock.patch.object(pm.subprocess, "run") as taskkill:
            self.assertFalse(pm.terminate_known_pid(proc.pid, birth + 0.01, identity_probe=lambda *_: "match"))
        taskkill.assert_not_called()
        self.assertIsNone(proc.poll())

    def test_verified_persisted_child_terminates(self):
        proc = self.spawn()
        birth = pm.creation_time(proc.pid)
        # The parent owns waitpid. Reap independently so POSIX can retire the
        # group rather than keeping its zombie leader in the process table.
        waiter = threading.Thread(target=proc.wait, daemon=True)
        waiter.start()
        try:
            self.assertTrue(pm.terminate_known_pid(proc.pid, birth))
        finally:
            if proc.poll() is None:
                proc.kill()
            waiter.join(5)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(pm.identity_state(proc.pid, birth), "absent")

    def test_owned_runner_and_child_group_both_exit(self):
        # Each Python payload has its own deadline, including on assertion failure.
        proc = self.spawn('''
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
    "import time, pathlib; end = time.monotonic() + 30\\nwhile time.monotonic() < end and not pathlib.Path('release-child').exists(): time.sleep(.05)"])
if os.name == 'posix':
    def finish(*_):
        child.wait(4)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, finish)
Path('tmp-pid').write_text(str(child.pid))
Path('tmp-pid').rename('child-pid')
# Only the SIGTERM handler waits for the child on POSIX.
if os.name == 'posix':
    time.sleep(12)
else:
    child.wait()
''')
        child_pid = int(self.wait_file("child-pid").read_text())
        try:
            self.assertTrue(pm.presence(child_pid))
            self.assertTrue(pm.terminate_tree(proc))
            self.assertIsNotNone(proc.poll())
            self.assertFalse(pm.presence(child_pid))
        finally:
            (self.root / "release-child").touch()
            deadline = time.monotonic() + 5
            while pm.presence(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)

    @unittest.skipUnless(os.name == "posix", "requires POSIX session groups")
    def test_inherited_session_is_never_signalled(self):
        proc = self.spawn(new_session=False)
        birth = pm.creation_time(proc.pid)
        with mock.patch.object(pm.os, "killpg", side_effect=AssertionError("foreign group signalled")):
            self.assertFalse(pm.terminate_known_pid(proc.pid, birth))
            self.assertFalse(pm.terminate_tree(proc))
        self.assertIsNone(proc.poll())

    @unittest.skipUnless(os.name == "posix", "requires POSIX session groups")
    def test_exited_leader_with_live_child_keeps_group_unverified(self):
        child_code = '''
import time
from pathlib import Path
Path('ready').touch()
end = time.monotonic() + 10
while not Path('finish').exists() and time.monotonic() < end:
    time.sleep(.01)
Path('done').touch()
'''
        proc = self.spawn(f"import subprocess,sys; subprocess.Popen([sys.executable, '-I','-B','-c',{child_code!r}])")
        try:
            self.wait_file("ready")
            proc.wait(timeout=5)
            self.assertIsNone(pm.presence(proc.pid))
            self.assertEqual(pm.identity_state(proc.pid, 123), "unknown")
            self.assertFalse(pm.terminate_tree(proc))
        finally:
            (self.root / "finish").touch()
            self.wait_file("done")


if __name__ == "__main__":
    unittest.main()
