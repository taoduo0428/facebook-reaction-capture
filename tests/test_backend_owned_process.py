"""Windows kernel tests. Every subject is a Python subprocess created here."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from facebook_backend.platform import owned_process as owned


def python(code):
    # A Windows venv launcher may keep an extra redirector process alive.
    return [sys._base_executable, '-I', '-S', '-c', code]


def spawn(code):
    return 'import subprocess; subprocess.Popen(' + repr(python(code)) + ')'


@unittest.skipUnless(sys.platform == 'win32', 'Windows Job Objects')
class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ready = self.root / 'ready'
        self.release = self.root / 'release'
        self.finished = self.root / 'finished'
        self.support_code = self.blocking_child(self.ready, self.finished)

    def blocking_child(self, ready, finished):
        # Bound every subject even when a test or cleanup fails.
        return (
            'import time\nfrom pathlib import Path\n'
            f'Path({str(ready)!r}).touch()\n'
            'deadline = time.monotonic() + 8\n'
            f'while not Path({str(self.release)!r}).exists() and time.monotonic() < deadline:\n'
            '    time.sleep(.01)\n'
            f'Path({str(finished)!r}).touch()'
        )

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('timed out waiting for Python fixture')
            time.sleep(.01)

    def assert_active(self, process, minimum=1):
        count = process.active_processes()
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, minimum)

    def launch(self, code, **options):
        process = owned.OwnedProcess.start(python(code), **options)
        self.addCleanup(process.close)
        if options.get('exclusive'):
            self.addCleanup(process.terminate_owned)
        return process

    def test_exit_code_streams_arguments_working_directory_and_environment(self):
        env = dict(os.environ, OWNED_PROCESS_TEST='值 with spaces')
        log = self.root / '测试 log'
        code = ('import os,sys;sys.stdout.buffer.write(os.environ["OWNED_PROCESS_TEST"].encode("utf-8")+b"\\n");'
                'sys.stderr.buffer.write(os.getcwd().encode("utf-8")+b"\\n");sys.exit(17)')
        with log.open('wb') as stream:
            process = owned.OwnedProcess.start(python(code), cwd=self.root, env=env,
                                               stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(process.close)
            self.assertEqual(process.wait(5), 17)
        lines = log.read_text(encoding='utf-8').splitlines()
        self.assertCountEqual(lines, [env['OWNED_PROCESS_TEST'], str(self.root)])
        self.assertEqual(process.poll(), 17)
        self.assertEqual(process.returncode, 17)
        self.until(lambda: process.active_processes() == 0)

    def test_exited_root_and_intermediate_do_not_hide_living_grandchild(self):
        process = self.launch(spawn(spawn(self.support_code)), exclusive=True)
        self.assertEqual(process.wait(5), 0)
        self.until(self.ready.exists)
        self.assert_active(process)
        self.release.touch()
        self.until(lambda: process.active_processes() == 0)
        self.assertTrue(self.finished.exists())

    def test_cancel_after_root_exit_terminates_only_its_exclusive_job(self):
        neighbor = self.launch('import time;time.sleep(8)', exclusive=True)
        process = self.launch(spawn(self.support_code), exclusive=True)
        self.assertEqual(process.wait(5), 0)
        self.until(self.ready.exists)
        self.assert_active(process)
        self.assertTrue(process.terminate_owned())
        self.assertEqual(process.active_processes(), 0)
        self.assertFalse(self.finished.exists())
        self.assert_active(neighbor)
        self.assertIsNone(neighbor.poll())

    def test_timeout_cancellation_for_mixed_tree_preserves_support(self):
        process = self.launch(spawn(self.support_code) + '; import time; time.sleep(8)')
        self.until(self.ready.exists)
        self.assert_active(process, minimum=2)
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(.01)
        self.assertFalse(process.terminate_owned())
        self.assertTrue(process.terminate_root())
        self.assert_active(process)
        process.close()
        self.assertIsNone(process.active_processes())
        self.release.touch()
        self.until(self.finished.exists)

    def test_native_capture_factory_distinguishes_runner_from_bootstrap_descendants(self):
        import facebook_capture_api as api
        from unittest.mock import Mock
        from facebook_backend.scheduler.production_native import NativeAttempt

        # Mimic a bootstrap which finishes while its independently owned support
        # process continues across both captures. No emulator, transport or API
        # service is started here.
        support = self.launch(spawn(self.support_code), exclusive=True)
        support.wait(5)
        self.until(self.ready.exists)

        def ensure_fixture(*, worker, process_callback, bootstrap_root_only):
            self.assertIs(bootstrap_root_only, True)
            process_callback(support)
            process_callback(None)

        self.enterContext(patch.object(api, 'BASE_DIR', self.root))
        self.enterContext(patch.object(api, 'ensure_emulator', side_effect=ensure_fixture))
        self.enterContext(patch.object(api, 'build_api_result', return_value={'fixture': True}))
        work_ready = self.root / 'work-ready'
        work_finished = self.root / 'work-finished'
        work_leaf = self.blocking_child(work_ready, work_finished)
        for name, work, expected in [('quiet', 'pass', 'stopped'), ('residual', spawn(spawn(work_leaf)), None)]:
            with self.subTest(name=name):
                native = NativeAttempt(Mock(), None, None)
                with patch.object(api, 'build_capture_command', return_value=python(work)):
                    result = api.run_capture_job({}, 'fixture-' + name, serial='fixture', run_dir=self.root / name,
                                                 process_callback=native.register, capture_process_callback=native.runner_started,
                                                 process_factory=native.spawn, transport_attempt_id='isolated-' + name)
                capture = native.owned_process
                self.addCleanup(capture.close)
                self.assertTrue(result['fixture'])
                self.assertEqual(capture.poll(), 0)
                if name == 'residual':
                    self.until(work_ready.exists)
                    self.assert_active(capture)
                else:
                    self.until(lambda: capture.active_processes() == 0)
                # No transport receipt is supplied. Exercise process proof only.
                self.assertEqual(native.proof(api), (expected, 'pending'))
                self.assert_active(support)
        self.release.touch()
        self.until(lambda: capture.active_processes() == 0)
        self.until(lambda: support.active_processes() == 0)
        self.assertTrue(work_finished.exists())

    def test_query_access_denied_is_unknown_even_after_normal_root_exit(self):
        import _winapi

        process = self.launch('pass')
        self.assertEqual(process.wait(5), 0)
        self.until(lambda: process.active_processes() == 0)
        current = _winapi.GetCurrentProcess()
        # The restricted duplicate still denotes this job, but lacks JOB_OBJECT_QUERY.
        restricted = subprocess.Handle(_winapi.DuplicateHandle(current, process._job, current, 0x8, False, 0))
        with patch.object(process, '_job', restricted):
            self.assertIsNone(process.active_processes())
            self.assertEqual(ctypes.get_last_error(), 5)  # ERROR_ACCESS_DENIED from the kernel
        restricted.Close()

    def test_assignment_access_denied_never_launches_unprotected_work(self):
        import _winapi

        kernel = owned._kernel()
        job = subprocess.Handle(owned._check(kernel.CreateJobObjectW(None, None)))
        self.addCleanup(job.Close)
        current = _winapi.GetCurrentProcess()
        # Supply only JOB_OBJECT_QUERY. CreateProcessW cannot atomically assign
        # the program to this handle and must fail before any user code runs.
        handle = _winapi.DuplicateHandle(current, job, current, 0x4, False, 0)
        with patch.object(kernel, 'CreateJobObjectW', return_value=handle):
            with self.assertRaises(OSError) as caught:
                owned.OwnedProcess.start(python('from pathlib import Path;Path(' + repr(str(self.ready)) + ').touch()'))
        self.assertEqual(caught.exception.winerror, 5)
        self.assertFalse(self.ready.exists())

    def test_closed_handle_and_failed_query_never_become_zero_proof(self):
        process = self.launch('pass')
        process.wait(5)
        for error in (PermissionError('denied'), OSError('invalid handle')):
            with patch.object(owned._kernel(), 'QueryInformationJobObject', side_effect=error):
                self.assertIsNone(process.active_processes())
        process.close()
        self.assertIsNone(process.active_processes())
        self.assertFalse(process.terminate_root())
        self.assertFalse(process.terminate_owned())


if __name__ == '__main__':
    unittest.main()
