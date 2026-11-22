"""Attempt-local native transport evidence. No emulator, host watcher or ADB is invoked."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from facebook_backend.platform import transport_receipt as proof

ROOT = Path(__file__).resolve().parents[1]
PS = (shutil.which('powershell') if os.name == 'nt' else None) or shutil.which('pwsh')


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve() / 'attempt-001'
        self.directory.mkdir()
        self.identity = dict(job_id='job-fixture', attempt_id='attempt-fixture', serial='fixture-1',
                             capture_port=18081, platform='win32', runner_pid=1001, started_at=time.time()-1)
        self.doc = {k: v for k, v in self.identity.items() if k != 'started_at'}
        self.doc.update(version=1, state='pending-watchdog', adb_state='device', errors=[],
                        attempt_dir=str(self.directory), observed_at=datetime.now(timezone.utc).isoformat(),
                        normal_proxy=':0', proxy_readback=':0', network_ready=True, ui_online=True,
                        required_reverse_ports=[18081], reverse_readback_ports=[18081],
                        watchdog_owner='production', watchdog_pid=None)
        self.host = dict(watchdog_pid=804, helper_pid=803, serial='fixture-1', capture_port=18081,
                         state='watchdog-running')

    def publish(self, **fields):
        (self.directory / proof.RECEIPT_NAME).write_text(json.dumps(dict(self.doc, **fields)), encoding='utf8')

    def verify(self):
        return proof.verify_transport_receipt(self.directory, **self.identity)

    def resume(self, host=None):
        with patch.dict(os.environ, {'SystemRoot': 'unused-system-root'}), patch.object(
                proof.subprocess, 'run', return_value=Mock(stdout=json.dumps(host or self.host).encode())) as run:
            outcome = proof.resume_windows_watchdog(self.directory, **{k: v for k, v in self.identity.items() if k != 'platform'})
        return outcome, run.call_count

    def test_windows_receipt_needs_matching_independent_watcher(self):
        self.assertFalse(self.verify())
        self.publish(normal_proxy='127.0.0.1:7897', proxy_readback='127.0.0.1:7897',
                     required_reverse_ports=[7897, 18081], reverse_readback_ports=[18081, 7897])
        self.assertFalse(self.verify())
        self.assertEqual(self.resume(), (True, 1))
        self.assertTrue(self.verify())
        self.assertEqual(self.resume(), (False, 0))  # duplicate handoff never launches twice
        path = self.directory / proof.HOST_RECEIPT
        data = json.loads(path.read_text())
        path.write_text(json.dumps(dict(data, attempt_id='old')))
        self.assertFalse(self.verify())

    def test_mismatched_watcher_is_held(self):
        self.publish()
        self.assertEqual(self.resume(dict(self.host, serial='other')), (False, 1))
        self.assertFalse(self.verify())

    def test_bad_runner_evidence_never_starts_watcher(self):
        cases = [dict(runner_pid=True), dict(runner_pid=44), dict(attempt_id='old'), dict(job_id='other'),
                 dict(serial='other'), dict(attempt_dir=str(self.directory.parent)), dict(state='failed'),
                 dict(errors=['capture-incomplete']), dict(adb_state='offline'), dict(proxy_readback='null'),
                 dict(network_ready=False), dict(ui_online=False), dict(watchdog_owner='batch'),
                 dict(reverse_readback_ports=[]), dict(reverse_readback_ports=[180810]),
                 dict(observed_at='2001-01-01T00:00:00Z'), dict(observed_at='3000-01-01T00:00:00Z')]
        for fields in cases:
            with self.subTest(fields=fields):
                self.publish(**fields)
                self.assertEqual(self.resume(), (False, 0))
        self.publish()
        os.utime(self.directory / proof.RECEIPT_NAME, (0, 0))
        self.assertEqual(self.resume(), (False, 0))

    def test_mac_requires_marker_status_and_exact_readback(self):
        self.identity['platform'] = 'darwin'
        self.doc.update(platform='darwin', state='restored', reverse_readback='absent', run_id='owner')
        self.publish()
        marker = self.directory / '.android-capture-owner'
        status = self.directory / 'android-capture-status.json'
        self.assertFalse(self.verify())
        marker.write_text('owner')
        status.write_text(json.dumps(dict(run_id='owner', exit_code=0, cleanup_errors=[])))
        self.assertTrue(self.verify())
        self.publish(reverse_readback='present')
        self.assertFalse(self.verify())
        self.publish()
        status.write_text(json.dumps(dict(run_id='owner', exit_code=2, cleanup_errors=[])))
        self.assertFalse(self.verify())
        status.write_text(json.dumps(dict(run_id='old', exit_code=0, cleanup_errors=[])))
        self.assertFalse(self.verify())

    def test_mac_api_child_publishes_its_own_success_and_failure_receipts(self):
        import facebook_capture_api as api
        child = r'''
import os, sys, types
import facebook_android_capture as cap
from unittest.mock import Mock
if os.name == 'nt':
    sys.modules['fcntl'] = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=Mock())
r = cap.CaptureRunner(cap.parse_args(sys.argv[1:]))
cap.tempfile.gettempdir = lambda: str(r.out.parent)
r.preflight = Mock()
r.start_transport = Mock()
r.proxy_dirty = r.reverse_owned = True
r.old_proxy = ':0'
r.adb = Mock(side_effect=[f'127.0.0.1:{r.args.capture_port}', '', ':0', '', 'device', ':0', ''])
r.run_collection = Mock(return_value='terminal')
r.analyze = Mock(return_value=2 if r.out.name == 'failed' else 0)
r.validate_result = Mock()
sys.exit(r.execute())
'''
        popen = subprocess.Popen
        def launch(command, **kwargs):
            # Windows venv python.exe is a launcher stub. This test emulates a
            # Darwin child, so start the interpreter process itself: the Popen
            # handle and the PID which signs the receipt must identify one process.
            exe = getattr(sys, '_base_executable', command[0]) if os.name == 'nt' else command[0]
            return popen([exe, '-c', child, *command[2:]], **kwargs)
        request = api.normalize_request({'post_url': 'https://www.facebook.com/1/posts/2'})
        for succeeded in (True, False):
            directory = self.directory / ('success' if succeeded else 'failed')
            owner = []
            with self.subTest(succeeded=succeeded), patch.object(api.sys, 'platform', 'darwin'), \
                    patch.object(api, 'ensure_emulator'), patch.object(api, 'build_api_result', return_value={}), \
                    patch.object(api.subprocess, 'Popen', side_effect=launch):
                def invoke():
                    return api.run_capture_job(request, 'job-fixture', 'fixture-1', run_dir=directory,
                                               transport_attempt_id='attempt-fixture', timeout_seconds=15,
                                               process_callback=lambda p: owner.append(p.pid) if p else None)
                if succeeded:
                    invoke()
                else:
                    with self.assertRaises(api.ApiError):
                        invoke()
                doc = json.loads((directory / proof.RECEIPT_NAME).read_text())
                identity = dict(self.identity, runner_pid=owner[0], platform='darwin', capture_port=doc['capture_port'])
                self.assertEqual(proof.verify_transport_receipt(directory, **identity), succeeded)
                self.assertEqual(doc['state'], 'restored' if succeeded else 'failed')

    def test_mac_helper_reads_only_restored_serial_proxy_and_reverse(self):
        from facebook_android_capture import CaptureRunner, parse_args, CaptureError
        args = parse_args(['--serial', 'fixture-1', '--post-url', 'https://www.facebook.com/1/posts/2',
                           '--output-dir', str(self.directory), '--capture-port', '18081'])
        runner = CaptureRunner(args)
        runner.proxy_dirty = runner.reverse_owned = True
        runner.old_proxy = ':0'
        for reads, success in ((['device', ':0', ''], True), (['offline', ':0'], False),
                               (['device', 'other'], False), (['device', ':0', 'error'], False),
                               (['device', ':0', 'usb tcp:18081 tcp:77'], False)):
            with self.subTest(reads=reads):
                runner.adb = Mock(side_effect=reads)
                if success:
                    runner._read_restored_transport()
                    self.assertEqual(runner.transport_readback['reverse_readback'], 'absent')
                else:
                    with self.assertRaises(CaptureError):
                        runner._read_restored_transport()

    @unittest.skipUnless(PS, 'PowerShell required for watchdog helper mock')
    def test_host_helper_requires_matching_new_watcher_and_preserves_existing(self):
        # Exercise the real helper, replacing only its transport and process table.
        helper = self.directory / 'facebook_resume_transport_watch.ps1'
        shutil.copyfile(ROOT / helper.name, helper)
        (self.directory / 'facebook_transport.ps1').write_text(r'''
function Start-FacebookTransportWatch {
    param($Serial,$CapturePort,$BasePath,[switch]$RequireVacant)
    if (-not $RequireVacant) { throw 'must preserve unknown processes' }
    return 517
}
function Get-CimInstance {
    $cmd='powershell.exe -File "' + (Join-Path $PSScriptRoot 'facebook_network_watch.ps1') + '" -Serial fixture-1 -CapturePort 18081'
    return @{ProcessId=517;ParentProcessId=$(if($env:WRONG_OWNER){1}else{$PID});Name='powershell.exe';CommandLine=$cmd}
}
''', encoding='utf-8-sig')
        for wrong in ('', 'yes'):
            proc = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-File', str(helper),
                                   '-Serial', 'fixture-1', '-CapturePort', '18081'],
                                  capture_output=True, timeout=30, env=dict(os.environ, WRONG_OWNER=wrong))
            with self.subTest(wrong_owner=wrong):
                self.assertEqual(proc.returncode == 0, not wrong, proc.stderr.decode(errors='replace'))
                if not wrong:
                    self.assertTrue(proof._watcher_running(json.loads(proc.stdout), 'fixture-1', 18081))
        harness = self.directory / 'vacant.ps1'
        (self.directory / 'facebook_network_watch.ps1').touch()
        harness.write_text(r'''
$ErrorActionPreference='Stop'
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:REAL_TRANSPORT,[ref]$t,[ref]$e)
$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Start-FacebookTransportWatch'},$true)
. ([scriptblock]::Create($fn.Extent.Text))
function Get-CimInstance { return @{Name='powershell.exe';CommandLine=('powershell -File "' + (Join-Path $PSScriptRoot 'facebook_network_watch.ps1') + '" -Serial fixture-1')} }
function Stop-FacebookTransportWatch { throw 'would kill an existing non-owned watcher' }
function Start-Process { throw 'would launch a duplicate watcher' }
try { Start-FacebookTransportWatch -Serial fixture-1 -CapturePort 18081 -BasePath $PSScriptRoot -RequireVacant; exit 9 }
catch { if (-not $_.Exception.Message.Contains('ownership is occupied')) { throw } }
''', encoding='utf-8-sig')
        proc = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-File', str(harness)],
                              capture_output=True, timeout=30,
                              env=dict(os.environ, REAL_TRANSPORT=str(ROOT/'facebook_transport.ps1')))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors='replace'))

    @unittest.skipUnless(PS, 'PowerShell required for runner finally mock')
    def test_real_windows_finally_with_mocked_helpers(self):
        # Parse the runner without running its top level. Exercise ONLY the actual finally,
        # receipt helpers and publication gate with mocked commands in our temporary tree.
        script = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:FIXTURE_RUNNER,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in 'Get-CaptureTransportReadback','Write-CaptureTransportReceipt') {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    . ([scriptblock]::Create($fn.Extent.Text))
}
$main=$ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] -and $_.Finally -and $_.Finally.Extent.Text.Contains('finalAdbReady') }
$body=$main.Finally.Extent.Text
$cleanup=[scriptblock]::Create($body.Substring(1,$body.Length-2))
$finish=[scriptblock]::Create($ast.EndBlock.Statements[-1].Extent.Text)
function Write-CaptureTiming {}
function Clear-FacebookCaptureMarker {}
function Ensure-FacebookAdbReady { return $case -ne 'offline' }
function Ensure-FacebookAndroidNetwork { return '42' }
function Ensure-FacebookReverseMappings { if ($case -eq 'restore-error') {throw 'mock'} }
function Set-FacebookNormalProxy { return ':0' }
function Confirm-FacebookUiOnline { return $case -ne 'ui-offline' }
function Get-FacebookAdbState { return 'device' }
function Get-FacebookNetworkConfig { return @{normal_proxy=':0'} }
function Invoke-FacebookAdbCommand { return @{Success=$true;Stdout=':0'} }
function Get-FacebookNormalReversePorts { return 18081 }
function Get-FacebookReverseList { if ($case -eq 'wrong-mapping') {return 'usb tcp:18081 tcp:444'}; return 'usb tcp:18081 tcp:18081' }
function Test-FacebookReverseMapping { return $true }
function Start-FacebookTransportWatch { throw 'test must not start any watchdog' }
$Serial='fixture-1'; $ProxyPort=18081
$env:FACEBOOK_BATCH_OWNS_WATCHDOG=''; $env:FACEBOOK_TRANSPORT_ATTEMPT_ID='attempt-fixture'; $env:FACEBOOK_TRANSPORT_JOB_ID='job-fixture'
foreach ($case in 'success','offline','restore-error','ui-offline','wrong-mapping','capture-error','analysis-error') {
    $RunDir=Join-Path $env:FIXTURE_ROOT $case
    $null=New-Item -ItemType Directory -Path $RunDir
    $captureCompleted=$case -ne 'capture-error'
    . $cleanup
    # A post-finally analysis exception never reaches the publication gate.
    if ($case -ne 'analysis-error') { try { . $finish } catch { if ($case -eq 'success') {throw} } }
}
'''
        fixture = self.directory / 'isolated.ps1'
        fixture.write_text(script, encoding='utf-8-sig')
        proc = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-File', str(fixture)],
                              env=dict(os.environ, FIXTURE_ROOT=str(self.directory),
                                       FIXTURE_RUNNER=str(ROOT/'run_facebook_capture.ps1')),
                              capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors='replace'))
        for case in ('success', 'offline', 'restore-error', 'ui-offline', 'wrong-mapping', 'capture-error'):
            with self.subTest(case=case):
                d = json.loads((self.directory / case / proof.RECEIPT_NAME).read_text(encoding='utf-8-sig'))
                self.assertEqual(d['state'], 'pending-watchdog' if case == 'success' else 'failed')
                self.assertEqual(d['attempt_dir'], str(self.directory / case))
                self.assertEqual(d['errors'] == [], case == 'success')
                if case == 'success':
                    self.assertEqual(d['reverse_readback_ports'], [18081])
                    self.assertEqual(d['watchdog_owner'], 'production')
        self.assertFalse((self.directory / 'analysis-error' / proof.RECEIPT_NAME).exists())
