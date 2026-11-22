import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPLORE = (ROOT / "explore_facebook_readonly.ps1").read_text(encoding="utf-8-sig")


def block(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def run_powershell(script):
    command = "pwsh"
    result = subprocess.run(
        [command, "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=script,
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=30,
    )
    if result.returncode:
        raise AssertionError(
            "PowerShell fixture failed with exit code %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, result.stdout, result.stderr)
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("PowerShell fixture produced no JSON output")
    return json.loads(lines[-1])


def fixture_script(functions, body):
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "function Start-Sleep { param([int]$Milliseconds = 0, [int]$Seconds = 0); if ($Milliseconds -gt 0) { $script:Sleeps += $Milliseconds } elseif ($Seconds -gt 0) { $script:Sleeps += ($Seconds * 1000) } }",
            "function Invoke-FacebookAdbQuiet { $script:TapCount++; return $true }",
            "function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }",
            *functions,
            body,
        ]
    )


class ReactionNavigationLatencyTests(unittest.TestCase):
    def test_click_helper_exists_and_reuses_ready_snapshot(self):
        self.assertIn("function Invoke-ReactionEntryAndSnapshot", EXPLORE)
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        script = fixture_script(
            [activate, helper],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Get-PageState([string]$Path) { return 'reaction-list' }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$result = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
[pscustomobject]@{ path = $result.Path; state = $result.State; dumps = $script:DumpCount; taps = $script:TapCount; fallback = $result.OriginalWaitFallback; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        self.assertEqual(script_result := run_powershell(script), {
            "path": "reaction-list.xml",
            "state": "reaction-list",
            "dumps": 1,
            "taps": 1,
            "fallback": False,
            "sleeps": [250],
        })

    def test_loading_transition_uses_existing_ready_wait_without_reclicing(self):
        self.assertIn("function Wait-ReactionListReady", EXPLORE)
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        ready = block(EXPLORE, "function Wait-ReactionListReady", "$DiagnosticsPath")
        script = fixture_script(
            [activate, helper, ready],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$script:StateIndex = 0
$script:States = @('reaction-list-loading', 'reaction-list-loading', 'reaction-list')
$WaitSeconds = 4
function Get-PageState([string]$Path) { $value = $script:States[[Math]::Min($script:StateIndex, $script:States.Count - 1)]; $script:StateIndex++; return $value }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$opened = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
$result = Wait-ReactionListReady $opened.Path
[pscustomobject]@{ state = $result.State; ready = $result.Ready; attempts = $result.Attempts; dumps = $script:DumpCount; taps = $script:TapCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["state"], "reaction-list")
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["dumps"], 2)
        self.assertEqual(result["taps"], 1)
        self.assertEqual(result["sleeps"], [250, 1000])

    def test_never_ready_keeps_original_click_budget_without_reclick(self):
        activate = block(EXPLORE, "function Activate-ReactionEntry", "function Reveal-NormalizedPostSummary")
        helper = block(EXPLORE, "function Invoke-ReactionEntryAndSnapshot", "function Find-EntryWithFeedScan")
        script = fixture_script(
            [activate, helper],
            """
$script:DumpCount = 0
$script:TapCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Get-PageState([string]$Path) { return 'placeholder-or-unknown' }
$node = [pscustomobject]@{ Label = 'reaction-icons-summary'; Bounds = '[920,700][1040,820]'; X1 = 920; Y1 = 700; X2 = 1040; Y2 = 820; Package = 'com.facebook.katana'; Class = 'android.view.ViewGroup'; LongClickable = $false }
$result = Invoke-ReactionEntryAndSnapshot $node 'reaction-list.xml'
[pscustomobject]@{ state = $result.State; fallback = $result.OriginalWaitFallback; dumps = $script:DumpCount; taps = $script:TapCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["state"], "placeholder-or-unknown")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["dumps"], 2)
        self.assertEqual(result["taps"], 1)
        self.assertEqual(result["sleeps"][0], 250)
        self.assertGreaterEqual(result["sleeps"][1], 3500)

    def test_detail_reveal_requires_new_snapshot_and_keeps_budget_on_miss(self):
        self.assertIn("function Invoke-ReactionDetailReveal", EXPLORE)
        helper = block(EXPLORE, "function Invoke-ReactionDetailReveal", "function Find-StrictTargetReactionEntry")
        script = fixture_script(
            [helper],
            """
$script:DumpCount = 0
$script:Sleeps = @()
$WaitSeconds = 4
function Dump-Ui([string]$Name) { $script:DumpCount++; return $Name }
$result = Invoke-ReactionDetailReveal 'reaction' 1
[pscustomobject]@{ path = $result.Path; early = $result.EarlyReady; dumps = $script:DumpCount; sleeps = @($script:Sleeps) } | ConvertTo-Json -Compress
""",
        )
        result = run_powershell(script)
        self.assertEqual(result["dumps"], 2)
        self.assertFalse(result["early"])
        self.assertGreater(result["sleeps"][1], 0)
        self.assertLessEqual(sum(result["sleeps"]), 4000)

    def test_strict_reveal_checks_source_guard_before_accepting_entry(self):
        strict = block(EXPLORE, "function Find-StrictTargetReactionEntry", "function Find-StrictTargetShareEntry")
        reveal = strict.index("Invoke-ReactionDetailReveal")
        guard = strict.index("Test-ExpectedSourceGuard", reveal)
        accepted = strict.index("if ($reveal.Entry -or $reveal.CommentEntry)", guard)
        self.assertLess(reveal, guard)
        self.assertLess(guard, accepted)
        self.assertIn("Find-CommentsDetailEntryNode", strict)
        self.assertNotIn("Find-CommentsEntryNode $path", strict[:accepted])

    def test_pagination_rate_controls_and_all_cooldown_remain_intact(self):
        pagination = block(EXPLORE, "function Scroll-ReactionAdaptive", "function Is-ReactionTabLabel")
        self.assertIn("Start-Sleep -Seconds $initialPaginationCooldownSeconds", pagination)
        self.assertIn("Invoke-ReactionTriggerStrategy $strategy", pagination)
        self.assertIn("Wait-ReactionMinCycle $cycleStarted $minCycleMs", pagination)
        self.assertIn("Reaction GraphQL rate limit circuit breaker", pagination)
        self.assertIn("Test-ExpectedSourceGuard $immediate 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("Test-ExpectedSourceGuard $polled 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("Test-ExpectedSourceGuard $refreshed 'reaction' 'reaction-pagination'", pagination)
        self.assertIn("$ReactionPipelineBurstSize", pagination)


if __name__ == "__main__":
    unittest.main()
