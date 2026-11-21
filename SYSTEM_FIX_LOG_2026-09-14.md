# System Fix Log — 2026-09-14

Purpose: trace every change made during the stability repair session requested by the user. The operating rule is minimal, evidence-based modification only: fix verified defects, preserve unrelated behavior, and keep enough detail for later rollback or audit.

## Protected scope

The following areas are intentionally out of scope unless a later defect proves they must change:

- `start_api_and_emulator.ps1` and the user's one-click startup flow.
- Strict Facebook target validation, fresh capture boundaries, `source_post_id` guards, analyzer source filtering, and completion semantics.
- Account/login state, AVD data, Facebook app data, credentials, proxy credentials, or historical run artifacts.
- Broad refactors, dependency changes, scheduler/database migrations, and performance tuning.

## Baseline before modifications

- Workspace change review was clean: no pending file modifications.
- Existing release-contract regression run before this session: 273 tests passed, 2 skipped.
- Verified defect A: persisted `queued` and safely recoverable `running` API jobs are converted to `failed` during API startup instead of being requeued.
- Verified defect B: after legacy UIAutomator failure, instrumentation fallback returns `helper-not-installed` without one bounded attempt to restore the helper, despite an existing `Ensure-FacebookUiDumpHelper` repair function.

## Change records

### Change 001 — Traceability log created

Files changed:

- `SYSTEM_FIX_LOG_2026-09-14.md` (new)

Reason:

- User explicitly requested a written record for every modification so later investigation can identify what changed, why, and how it was validated.

Behavioral impact:

- None. Documentation only.

Rollback:

- Delete this file only if the audit record is intentionally being removed.

Validation:

- Not applicable; no runtime code changed.

### Change 002 — Safe API restart recovery for recoverable jobs

Files changed:

- `facebook_capture_api.py`
- `test_facebook_capture_api.py`

Reason:

- Persisted jobs in `queued` state, and `running` jobs whose prior capture process is already verified stopped/absent, were being converted to `failed` on API startup even though the capture lease was known safe to reuse.

Behavioral impact:

- A persisted `queued` job with no process evidence is restored as `queued` and placed back on the existing scheduler queue.
- A persisted `running` job is requeued only after startup proves there is no remaining capture-process evidence. Existing attempt/checkpoint paths are preserved and the normal scheduler creates a fresh attempt on the next assignment.
- Live worker ownership (`worker_id`, `serial`, `started_at`) is cleared before requeue; prior ownership is retained as `last_worker_id`/`last_serial` for auditability.
- Jobs with a still-running or identity-unverified process remain fail-closed and continue to block worker takeover. This safety behavior was intentionally preserved.

Rollback:

- Revert only the restart branch in `JobManager._load_persisted_jobs` and the two restart-recovery tests. No startup scripts or capture/analyzer logic depend on the new branch.

Validation:

- Targeted restart tests passed, including queued recovery, verified-stopped running recovery, legacy interrupted-running failure behavior, and identity-unverified fail-closed behavior.
- Full release regression set passed: 276 tests, 2 skipped, 0 failures.

### Change 003 — One-shot UI dump helper self-healing

Files changed:

- `facebook_transport.ps1`
- `test_facebook_capture_orchestrator.py`

Reason:

- Historical failed jobs contained `legacy-dump-failed;helper-not-installed`, while `Ensure-FacebookUiDumpHelper` already provided a bounded repair path. `Invoke-FacebookFastUiDump` previously returned immediately instead of using it.

Behavioral impact:

- The normal path is unchanged when the helper is already installed.
- If the helper is missing, `Invoke-FacebookFastUiDump` invokes `Ensure-FacebookUiDumpHelper` exactly once. A failed repair still returns `helper-not-installed`; there is no retry loop.
- The instrumentation command and output validation remain unchanged after a successful repair.

Rollback:

- Remove only the bounded `Ensure-FacebookUiDumpHelper` call from `Invoke-FacebookFastUiDump` and its contract test.

Validation:

- The dedicated contract test confirms exactly one self-heal call, only after the installed check and before instrumentation.
- `facebook_transport.ps1` successfully parsed as a PowerShell script block after the change.
- `node --check web/app.js` and `node --check web_v2/app.js` both passed.
- Source whitelist export succeeded with 87 files (`tools/export_source.py`).
- Full release regression set passed: 276 tests, 2 skipped, 0 failures.

### Change 004 — Clear stale recovery/failover error on new attempt assignment

Files changed:

- `facebook_capture_api.py`
- `test_facebook_capture_api.py`

Reason:

- A safely requeued job intentionally keeps a human-readable prior-attempt error while it is waiting in `queued`, but that message previously survived after the scheduler assigned a fresh attempt. The UI can display `job.error`, so a successfully resumed running/completed job could still look failed.

Behavioral impact:

- Prior recovery/failover context remains visible while the job is queued.
- Only when `_assign_job_to_worker` successfully changes the job to `running` is `job.error` cleared. The new attempt therefore starts with no stale failure message.
- `recovery_state`, attempt directories, checkpoint evidence, prior worker identity, and all fail-closed PID/lease protections remain unchanged.

Rollback:

- Remove only the `job["error"] = None` assignment and its focused assertion in the restart-recovery test.

Validation:

- Targeted restart/failover regression set passed: 17 tests, 0 failures.
- Full release regression set passed after this change: 276 tests, 2 skipped, 0 failures.

### Change 005 — Align stable cold-boot timeout with real host behavior

Files changed:

- `start_facebook_stable.ps1`
- `test_facebook_capture_orchestrator.py`

Reason:

- A real `START.cmd` cold-start run launched `emulator-5554`, restored the upstream/reverse transport successfully, then failed at the 120-second `sys.boot_completed` deadline.
- A direct ADB probe immediately after that failure returned `sys.boot_completed=1`, proving the AVD was slow rather than dead.
- `add_facebook_worker.ps1` already uses a 240-second boot budget, so the stable launcher was the outlier inside the same project.

Behavioral impact:

- The default `BootTimeoutSeconds` in `start_facebook_stable.ps1` is raised from 120 to 240 seconds.
- The existing bounded deadline loop, emulator arguments, early reverse setup, upstream proxy behavior, API/UI orchestration, and fail-closed readiness checks are unchanged.
- Explicit callers can still override `-BootTimeoutSeconds` exactly as before.

Rollback:

- Restore the stable launcher default from 240 to 120 seconds and remove the focused timeout-contract assertion.

Validation:

- Targeted orchestrator regression passed: 112 tests, 0 failures.
- Historical 10-module core regression passed: 277 tests, 2 skipped, 0 failures. This is the prior 276-test baseline plus the new cold-boot timeout contract test.
- A broader `unittest discover` run executed 413 tests and reported 5 unrelated pre-existing/environment-sensitive failures in release-gate fixtures and the protected `web/index.html` hash check; none touched the cold-boot path, and no unrelated files were modified to force those tests green.
- Real one-click cold-start runtime verification is still pending after this log update.

### Change 006 — Keep background one-click emulator startup out of Windows Session 0

Files changed:

- `start_api_and_emulator.ps1`
- `test_ui_v2_and_safety.py`

Reason:

- A real one-click run initiated through the CodexPro/background execution bridge started `emulator.exe` in Windows Session 0 (`Services`). The process remained present but `emulator-5554` never registered with ADB, and the bounded 240-second startup correctly failed with `ADB device did not become ready`.
- The project already contains `launch_visible_in_user_session.ps1`, whose explicit purpose is to launch worker-01/worker-02 through the logged-in Explorer token on `winsta0\\default`; `FACEBOOK_LINK_SUPPORT.md` also requires this path for non-interactive/background emulator startup.
- The original one-click implementation predates that background-session requirement and called `start_facebook_stable.ps1` directly regardless of the caller session.

Behavioral impact:

- `start_api_and_emulator.ps1` now detects its own Windows session only when the configured primary emulator is unavailable.
- When running in Session 0, it invokes the existing `launch_visible_in_user_session.ps1 -WorkerId ...`, waits up to the same bounded 240-second emulator startup budget for ADB readiness, and only then calls the existing stable starter for transport/helper initialization.
- Normal desktop/double-click execution outside Session 0 remains on the original direct stable-start path.
- Session 0 fails closed for worker IDs without an existing interactive-session launcher instead of silently launching an emulator in the Services session.
- `START.cmd`, emulator arguments, AVD/account state, proxy/reverse behavior, capture logic, target validation, API job scheduling, and UI submission behavior are unchanged.

Rollback:

- Remove the Session 0 interactive-launch branch and `EmulatorBootTimeoutSeconds` passthrough from `start_api_and_emulator.ps1`, then remove the focused one-click contract test.

Validation:

- Windows one-click runtime contract tests passed: 11 tests, 0 failures.
- `start_api_and_emulator.ps1` PowerShell parser check passed.
- Historical 10-module core regression passed: 277 tests, 2 skipped, 0 failures.
- The full `test_ui_v2_and_safety` module still contains the pre-existing protected `web/index.html` hash mismatch; that unrelated file was not modified to force the safety baseline green.
- Real Session 0 -> interactive-user-session runtime verification remains pending after this log update.

### Change 007 — Fail closed on untriggered Reaction capture and default to lightweight evidence

Files changed:

- `facebook_capture_api.py`
- `run_facebook_capture.ps1`
- `web_v2/index.html`
- `test_facebook_capture_api.py`
- `test_facebook_capture_orchestrator.py`

Reason:

- A real URL run resolved and strictly verified the target post and observed `reported_total_count=149`, but no Reaction query was triggered and zero identities were enumerated. The API nevertheless accepted the `query-not-triggered` visibility path as an empty terminal result.
- The same run also showed that default PCAP and full raw-MITM persistence add large diagnostic artifacts even though the final Reaction-user result depends on the safe GraphQL capture/analysis path, not on retaining every raw evidence layer.
- The strict-target UI recovery path reopened the same already-verified post twice after the same `no-reaction-count-after-strict-target-detail-scan` failure, adding avoidable latency before fallback.

Behavioral impact:

- `query-not-triggered` is no longer accepted as a successful empty Reaction result when Facebook reports a positive reaction total. The result now fails closed as incomplete instead of returning `completed/terminal` with zero identities.
- Default optional artifacts are now `safe_capture` and `progress`. Raw PCAP and full MITM archives remain available as explicit opt-in diagnostic artifacts.
- On Windows, when full MITM persistence is not selected, the capture command now passes `-DisableRawHttpCapture`; the live MITM proxy and safe GraphQL addon remain active, so Reaction parsing and strict target validation are preserved while the heavy raw-flow writer is skipped.
- V2 UI defaults now leave `原始 PCAP` and `MITM 解密流` unchecked while keeping `脱敏 GraphQL` and `实时进度快照` checked.
- The bounded same-target Reaction-entry reopen count is reduced from 2 to 1. Source guards, target re-verification, fallback, pagination, and result completeness checks are unchanged.

Rollback:

- Restore `query-not-triggered` handling in `_empty_reaction_visibility_allowed`, restore `DEFAULT_SAVE_ARTIFACTS` to all optional groups, remove the Windows `-DisableRawHttpCapture` selection branch, re-check the two V2 raw-artifact checkboxes, and restore `$primaryReactionRetryLimit` from 1 to 2.

Validation:

- Focused result/command tests passed.
- `test_facebook_capture_api` + `test_facebook_capture_orchestrator`: 213 tests passed, 0 failures.
- Historical 10-module release regression: 277 tests passed, 2 skipped, 0 failures.
- `node --check web/app.js` and `node --check web_v2/app.js` passed.
- `run_facebook_capture.ps1` PowerShell parser check passed.
- `test_ui_v2_and_safety` still has the pre-existing protected `web/index.html` hash mismatch; this change modified `web_v2/index.html`, not the protected legacy `web/index.html`.

### Change 008 — Add one-click full Facebook runtime stop

Files changed:

- `STOP.cmd` (new)
- `stop_all_facebook.ps1` (new)
- `release-files.txt`
- `test_facebook_capture_orchestrator.py`

Reason:

- Stopping only API/UI ports is insufficient once multiple Facebook emulator workers exist. The stop action must follow `facebook_workers.json` so newly configured workers are included without hard-coded serials or AVD names.
- The user requested one one-click stop entry that shuts down the Facebook runtime completely before a later `START.cmd`, while avoiding unrelated Android emulators and unrelated system/global proxy processes.

Behavioral impact:

- `STOP.cmd` delegates to `stop_all_facebook.ps1` from the project directory.
- The stop script validates and reads every worker in `facebook_workers.json`, stops project transport watchdogs, project-owned API/UI/capture/MITM/Frida host processes, then requests shutdown for every configured worker emulator.
- Remaining emulator/qemu processes are force-stopped only when their `-avd` name matches a configured Facebook worker AVD. There is no hard-coded `emulator-5554`/worker-02 dependency, so future workers added to the config are included automatically.
- ADB server is intentionally left running, and unrelated Android emulators or global/system proxy processes are not targeted.
- `STOP.cmd` and `stop_all_facebook.ps1` are included in the source-release whitelist.

Rollback:

- Delete `STOP.cmd` and `stop_all_facebook.ps1`, remove their two `release-files.txt` entries, and remove the focused stop-runtime contract test/constants.

Validation:

- Dedicated STOP runtime contract test passed.
- Full `test_facebook_capture_orchestrator` module passed: 113 tests, 0 failures.
- Historical 10-module release regression passed after the new STOP contract: 278 tests, 2 skipped, 0 failures.
- `stop_all_facebook.ps1` was syntax-parsed without executing the stop action.
- Source whitelist export succeeded with 89 files (`dist/facebook-source-20260915-144015.zip`).
- Real destructive stop/start acceptance was intentionally not executed during creation so the currently running user emulator/session was not interrupted.

## Session result

- `START.cmd` itself remains unchanged; the normal desktop one-click path remains the intended launch path.
- `STOP.cmd` now provides the matching Windows one-click full stop and follows configured workers dynamically.
- Strict target validation and `source_post_id` guards remain preserved. Completion semantics were intentionally tightened only for the verified false-success case where Reaction enumeration was never triggered despite a positive reported total.
- Default diagnostic evidence is lighter: final CSV/report, safe GraphQL capture, and progress remain; raw PCAP/full MITM are opt-in.
- Runtime changes are covered by focused tests and the release regression baseline.
