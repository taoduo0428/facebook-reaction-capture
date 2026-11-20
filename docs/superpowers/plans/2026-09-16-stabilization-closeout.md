# Facebook stabilization closeout implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded tasks. The user explicitly requests parallel agents with separate file ownership; preserve the existing dirty stabilization branch and the live service state.

**Goal:** Finish cancellation and recovery acceptance, validate sequential real URLs and measured waits, then deliver reviewed source and accurate Windows/macOS instructions.

**Architecture:** Keep the existing local HTTP API, serial primary/standby JobManager, Windows PowerShell capture runner and native macOS Python runner. Cancellation must prove process termination, restore owned transport, and only then release the capture lease. Preserve strict target matching, per-job deduplication, terminal-page completeness and rate-limit handling.

**Tech stack:** Python 3.12+, Windows PowerShell, Android SDK/ADB, existing Frida/mitmproxy transport, unittest, Node syntax checks, GitHub Actions.

## Baseline and boundaries

- Existing branch: `codex/stabilization-20260915`; all pre-existing tracked/untracked changes are retained.
- API 8765 and UI 8766 have identified project process owners. Initial API health: ready, idle, no queued/cancelling jobs, available lease. Primary emulator-5554 is booted with normal proxy; standby-02 is currently offline.
- Root agent exclusively owns production API restarts, emulator actions and real job submissions. Agents use isolated fixture directories and no live ADB/network mutation.
- Historical counts and logs locate relevant evidence; they do not prove the final source passes.
- Publication means updating the existing repository's reviewable stabilization branch. Do not change repository visibility/license or overwrite main. Inspect exact source whitelist and secret scan before any push.
- Updated user requirement: every new child uses explicit `gpt-5.6-luna`, effort `max`, and no inherited history. Accept its work only after checking the persisted runtime settings.

## Failure map

| Boundary | Failure to test | Required behavior |
| --- | --- | --- |
| JobManager / process tree | cancellation races, stale process identity | prove owned process stopped; retain uncertain lease otherwise |
| API / transport helper | timeout, proxy changed externally, watchdog failure | bounded cleanup, preserve foreign state, explicit error, no unsafe next job |
| runner / device | ADB or app failure | bounded recovery, truthful state, subsequent task remains possible |
| navigation / data | wrong post, partial tail, malformed capture | preserve target and completeness failures; never convert to empty success |
| load / latency | repeated startup, fixed sleeps, slow external response | reuse verified healthy state; bounded readiness checks; preserve throttling |
| release / deployment | omitted modules, local credentials, wrong architecture | whitelist exact source; executable platform-specific instructions |

## Task 1: cancellation and failure lifecycle

Files: `facebook_capture_api.py`, `facebook_cancel_transport_cleanup.ps1`, `test_facebook_cancel_transport.py`, and isolated cancellation fixtures. Read `facebook_android_capture.py` for macOS lifecycle consistency; coordinate any cross-file edits first.

- [x] Run `rtk proxy .venv/Scripts/python.exe -m unittest test_facebook_cancel_transport test_facebook_capture_api` and retain output/counts (covered by the 548-test workflow-equivalent run; 2 intentional skips overall).
- [x] Trace `_terminate_tree`, `_cleanup_job_transport`, `_mark_process_stopped_locked`, restored-job reconciliation and final callbacks. Independent review found additional fail-open/error-shape branches; their fixes and fresh acceptance remain required.
- [ ] Reproduce any concrete defect with a bounded behavioral fixture before a minimal fix; keep success, exception, cleanup timeout, external proxy and restart recovery cases.
- [ ] Return exact changed files, root cause, test evidence and remaining real-device acceptance requirements.

## Task 2: performance and navigation

Files: `explore_facebook_readonly.ps1`, `run_facebook_capture.ps1`, `test_facebook_navigation_latency.py`, `test_facebook_navigation_guards.py`, `test_facebook_unpin_reuse.py`. Changes to shared transport/startup files require explicit ownership transfer.

- [x] Trace entry, target-open, reaction-entry and between-job app lifecycle; use recent capture logs for stage duration evidence.
- [x] Run `rtk proxy .venv/Scripts/python.exe -m unittest test_facebook_navigation_latency test_facebook_navigation_guards test_facebook_unpin_reuse test_facebook_capture_orchestrator` (included in the current 548-test run).
- [x] Remove a fixed wait only if a verified bounded readiness predicate replaces it; retain retry backoff, rate-limit cooldown and minimum scroll cadence.
- [x] Prove ready state returns promptly and delayed/failed state is handled; fixtures passed and the live batch retained per-stage timings without attributing cache differences to code.

## Task 3: live cancellation, failure recovery and sequential URLs (root)

Files: local-only `evidence/real-tests-20260916/` validation scripts/results and `VALIDATION_2026-09-16.md`.

- [x] Strengthen cancellation acceptance to record owned process identities, capture-port owners, worker/lease state, normal proxy readback and emulator boot state. New reports also record source hashes and reject concurrent source changes.
- [ ] Recheck idle health and process ownership immediately before an API-only restart if source changed. Never restart based solely on a polling timeout.
- [ ] Cancel one newly owned task after capture transport activates; require stopped process, no owned descendant/port residual, restored transport, idle worker and available lease.
- [ ] Run a low-volume complete task immediately after cancellation on the same worker, with no manual transport repair or standby substitution hidden as success.
- [ ] Verify a bounded injected runner failure using an isolated real HTTP fixture, then execute another task successfully. The fresh strict-target failure and transport cleanup passed, but the same-worker Reel follow-up was not currently visible; keep this item open until a current-source follow-up completes.
- [x] Run one sequential representative set covering ordinary numeric post, share/p, pfbid, group permalink and Reel if accessible. `batch_d68121ac9c464f00` completed the run and retained partial evidence for three inaccessible/entry-failed targets; no five-for-five claim is made.
- [x] Validate aggregate rows use job ID as the post boundary; the batch has 476 unique `(job_id, user_id)` pairs and preserves 238 repeated users across the two successful jobs.

## Task 4: deployment and README (separate documentation owner)

Files: `WINDOWS_DEPLOYMENT.md`, `macos/QUICKSTART.md`, `macos/CODEX_DEPLOYMENT_RUNBOOK.md`, `macos/README.md`, `README.md`, `API_USAGE.md`.

- [x] Check every named command, config key and entrypoint against source. Fix Windows ABI examples, CA provisioning, foreground server commands, shell/path/permission distinctions and existing-state preservation.
- [x] Provide Python/SDK/AVD/Facebook/Frida/CA setup, startup/API usage, health/complete-capture acceptance and specific troubleshooting for Windows and Apple Silicon macOS.
- [x] Describe real hardware validation separately from platform unit tests and hosted CI; no invented Mac acceptance.
- [x] Verify relative links and command help where non-mutating. `check_delivery_docs.py` accepted 34 local links and 5 shell files.

## Task 5: integration, release and final audit

Files: `.github/workflows/check.yml`, `release-files.txt`, `.gitignore`, `CHANGELOG.md`, `VALIDATION_2026-09-16.md` and source export.

- [x] Review each implementation against the task contract, then separately inspect safety/code quality before accepting it.
- [x] Run the final CI-equivalent module list, changed PowerShell parsers, both `node --check` commands, `pip check` and `git diff --check`.
- [x] Cross-check each release manifest entry exists, is admitted to Git and excludes credentials, runtime state, logs, APKs, CA private material and AVD files.
- [x] Export source; verify archive integrity, member set and manifest hashes; run portable checks in a fresh extraction.
- [ ] Inspect remote identity/branch state, commit exact reviewed source, push the stabilization branch, and inspect hosted CI if credentials and network permit.
- [ ] Audit every user requirement against current evidence. Keep Goal active for any unmet acceptance, including externally blocked requirements; report exact limits.

## Resume checkpoint

### Hosted CI follow-up

- [ ] Reproduce `ReactionSummaryIconFixtureTests` under a cp1252 subprocess default. Both hosted jobs in run `35095201386` failed in these fixtures; macOS returned non-JSON output, not a missing PowerShell executable.
- [ ] Replace the fixture's stdin command transport with UTF-16LE Base64 `-EncodedCommand` and explicit UTF-8 JSON output, keeping all selector cases enabled.
- [ ] Run the fixture regressions and workflow-equivalent suites, publish the narrow follow-up commit, and check both hosted jobs before declaring CI passing.
- [ ] Re-export and verify the final source archive after the follow-up source/documentation changes. Keep unresolved live target failures separate from hosted contract checks.

Earlier real cancellation and strict-target failure recovery passed. The five-type batch `batch_b27bda2739cc43ff` was 4/5: the pfbid Feed timestamp used Chinese sharing-audience text, which the English-only promotion regex missed. Preserve that failure record. A 119-file candidate ZIP passed 524 tests (2 skips), but predates final fixes and is not the final artifact.

The next acceptance uses the same original pfbid, then fresh cancellation/failure recovery and the full five-type sequence after API/analyzer/navigation fixes. All three repair agents were interrupted during session recovery; their file changes and RED evidence were preserved and fresh explicit Luna Max agents now finish those same bounded tasks. Do not mistake interrupted work or an older archive for completed acceptance.
