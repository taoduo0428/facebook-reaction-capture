"""Bounded PowerShell tests for the local mitmdump TCP readiness gate."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent
RUN_SOURCE = (ROOT / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
BLOCK_START = RUN_SOURCE.index("    $mitmStartError = $null")
BLOCK_END = RUN_SOURCE.index("\n    Set-FacebookCaptureMarker", BLOCK_START)
MITM_START_BLOCK = RUN_SOURCE[BLOCK_START:BLOCK_END]


def _powershell_executable():
    return shutil.which("powershell.exe") or shutil.which("pwsh")


def _run_readiness_scenario(scenario):
    powershell = _powershell_executable()
    if not powershell:
        raise unittest.SkipTest("PowerShell is required for readiness scenarios")

    prefix = f"""
$ErrorActionPreference = 'Stop'
$script:Scenario = '{scenario}'
$script:FakeNow = [datetime]'2026-01-01T00:00:00Z'
$script:ProbeCount = 0
$script:StartCount = 0
$script:StopCount = 0
$script:SleepCalls = @()
$script:CurrentProcess = $null
$script:Listener = $null
$portPicker = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$portPicker.Start()
$ProxyPort = ([System.Net.IPEndPoint]$portPicker.LocalEndpoint).Port
$portPicker.Stop()
$MitmdumpExe = 'mitmdump-stub.exe'
$RunDir = $PWD.Path
$StdoutLog = 'stdout.log'
$StderrLog = 'stderr.log'
$mitmArgs = @()
$Addon = 'facebook_capture_addon_safe.py'
$RawAddon = 'facebook_raw_flow_writer.py'
$listeners = @()

function Get-Date {{
    param([string]$Format)
    if ($Format) {{ return $script:FakeNow.ToString($Format) }}
    return $script:FakeNow
}}

function Start-Sleep {{
    param([int]$Seconds = 0, [int]$Milliseconds = 0)
    $delta = if ($Milliseconds -gt 0) {{ $Milliseconds }} else {{ $Seconds * 1000 }}
    $script:FakeNow = $script:FakeNow.AddMilliseconds($delta)
    $script:SleepCalls = @($script:SleepCalls) + @([pscustomobject]@{{ seconds = $Seconds; milliseconds = $Milliseconds }})
}}

function Start-Process {{
    param(
        [string]$FilePath,
        [object]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$WindowStyle,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [switch]$PassThru
    )
    $script:StartCount++
    $process = [pscustomobject]@{{ Id = 4242; HasExited = ($script:Scenario -eq 'exit') }}
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {{ param([int]$TimeoutMilliseconds) $this.HasExited = $true }}
    $script:CurrentProcess = $process
    return $process
}}

function Stop-Process {{
    param([int]$Id, [switch]$Force, [string]$ErrorAction)
    $script:StopCount++
    if ($script:CurrentProcess) {{ $script:CurrentProcess.HasExited = $true }}
}}

function Get-CapturePortListenerPids {{
    $script:ProbeCount++
    if ($script:Scenario -eq 'unknown') {{ return @(9999) }}
    if ($script:Scenario -eq 'exit' -or $script:Scenario -eq 'timeout') {{ return @() }}
    $readyAfter = if ($script:Scenario -eq 'delayed') {{ 65 }} else {{ 1 }}
    if ($script:ProbeCount -lt $readyAfter) {{ return @() }}
    if (-not $script:Listener) {{
        $script:Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $ProxyPort)
        $script:Listener.Start()
    }}
    if ($script:Scenario -eq 'child') {{ return @(5252) }}
    return @($script:CurrentProcess.Id)
}}

function Get-CimInstance {{
    param([string]$ClassName, [string]$Filter, [string]$ErrorAction)
    if ($script:Scenario -eq 'child') {{
        return [pscustomobject]@{{ ProcessId = 5252; ParentProcessId = 4242; CommandLine = 'python -m mitmproxy facebook_capture_addon_safe.py' }}
    }}
    return $null
}}

function Clear-StaleProjectCaptureListener {{ return @() }}
"""
    suffix = r'''
try {
''' + MITM_START_BLOCK + r'''
    $outcome = [ordered]@{
        status = 'ready'
        attempt = [int]$mitmAttempt
        ready_elapsed_ms = [int]$readyElapsedMs
        poll_count = [int]$script:ProbeCount
        backoff_count = @($script:SleepCalls | Where-Object { $_.seconds -eq 2 }).Count
        stop_count = [int]$script:StopCount
    }
    Write-Output ('RESULT_JSON=' + ($outcome | ConvertTo-Json -Compress))
}
catch {
    $outcome = [ordered]@{
        status = 'failed'
        error = [string]$_.Exception.Message
        attempt = [int]$script:StartCount
        poll_count = [int]$script:ProbeCount
        backoff_count = @($script:SleepCalls | Where-Object { $_.seconds -eq 2 }).Count
        stop_count = [int]$script:StopCount
    }
    Write-Output ('ERROR_JSON=' + ($outcome | ConvertTo-Json -Compress))
    exit 1
}
finally {
    if ($script:Listener) { $script:Listener.Stop() }
}
'''
    encoded = base64.b64encode((prefix + suffix).encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [powershell, "-NoProfile", "-EncodedCommand", encoded],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _result_payload(result):
    lines = (result.stdout + "\n" + result.stderr).splitlines()
    payload_lines = [line for line in lines if line.startswith(("RESULT_JSON=", "ERROR_JSON="))]
    if not payload_lines:
        raise AssertionError(f"PowerShell emitted no result payload:\n{result.stdout}\n{result.stderr}")
    return json.loads(payload_lines[-1].split("=", 1)[1])


class ReadinessSourceContractTests(unittest.TestCase):
    def test_startup_uses_bounded_owned_tcp_probe(self):
        self.assertNotIn("Start-Sleep -Seconds 3", MITM_START_BLOCK)
        self.assertIn("AddSeconds($mitmReadyTimeoutSeconds)", MITM_START_BLOCK)
        self.assertIn("$mitmReadyTimeoutSeconds = 10", MITM_START_BLOCK)
        self.assertIn("ConnectAsync('127.0.0.1', $ProxyPort)", MITM_START_BLOCK)
        self.assertIn("Get-CapturePortListenerPids $ProxyPort", MITM_START_BLOCK)
        self.assertIn("non-project listener", MITM_START_BLOCK)
        self.assertIn("Start-Sleep -Seconds 2", MITM_START_BLOCK)
        self.assertIn("ready_elapsed_ms=", MITM_START_BLOCK)


@unittest.skipUnless(_powershell_executable(), "Windows PowerShell required")
class ReadinessScenarioTests(unittest.TestCase):
    def test_ready_listener_returns_before_three_second_budget(self):
        result = _run_readiness_scenario("early")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "ready")
        self.assertLess(payload["ready_elapsed_ms"], 3000)
        self.assertEqual(payload["backoff_count"], 0)

    def test_delayed_listener_keeps_polling_until_ready(self):
        result = _run_readiness_scenario("delayed")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "ready")
        self.assertGreater(payload["ready_elapsed_ms"], 3000)
        self.assertLess(payload["ready_elapsed_ms"], 10000)
        self.assertGreaterEqual(payload["poll_count"], 65)
        self.assertEqual(payload["backoff_count"], 0)

    def test_owned_python_child_listener_is_accepted(self):
        result = _run_readiness_scenario("child")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "ready")

    def test_process_exit_fails_and_preserves_retry_backoff(self):
        result = _run_readiness_scenario("exit")
        self.assertNotEqual(result.returncode, 0)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("exited before", payload["error"])
        self.assertEqual(payload["attempt"], 3)
        self.assertEqual(payload["backoff_count"], 3)

    def test_timeout_fails_and_preserves_retry_backoff(self):
        result = _run_readiness_scenario("timeout")
        self.assertNotEqual(result.returncode, 0)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("within 10 seconds", payload["error"])
        self.assertEqual(payload["attempt"], 3)
        self.assertEqual(payload["backoff_count"], 3)
        self.assertEqual(payload["stop_count"], 3)

    def test_unknown_listener_is_rejected(self):
        result = _run_readiness_scenario("unknown")
        self.assertNotEqual(result.returncode, 0)
        payload = _result_payload(result)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("non-project listener", payload["error"])


if __name__ == "__main__":
    unittest.main()
