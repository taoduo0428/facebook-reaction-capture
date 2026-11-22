# Facebook stabilization execution record

## Scope and baseline

Objective: restore general post Reaction navigation and reliable capture, reduce measured unnecessary work, preserve existing GraphQL/MITM/worker/API contracts, and complete a usable Python API integration path.

The authoritative request is the September 15 goal attachment and `CODEXPRO_FACEBOOK_MASTER_HANDOFF_2026-09-15.md`. Historical PDFs are supporting context only.

Baseline commit: `553d4bf`; pre-existing modifications in ten tracked files belong to the prior work. They are preserved. Source rollback copies: `backups/stabilization-20260915-155505/` (14 files). Work continues on `codex/stabilization-20260915` without publishing.

Latest observed failed job: `fb_756e820d581647ab`, submitted at 06:43:36Z and failed at 06:46:12Z, `reaction capture is incomplete: query-not-triggered`. Primary target: `https://www.facebook.com/share/p/1Jetj5opcE/`. This is a navigation/enumeration failure, not evidence of an empty list.

## Execution and acceptance

- [x] Read main handoff, inspect current changes, identify latest failure, confirm no live capture before starting the existing runtime.
- [x] Trace failed UI snapshots/logs against selector logic and history; reproduce the specific defect with an existing test harness before changing production logic.
- [x] Repair the smallest general navigation/wait defect, retaining strict source identity and comment exclusion; verify positive post cases and negative comment cases.
- [x] Run real API submission/status/result/download for the reported target and previously supported contrasting URLs. Record timings, target identities, users and terminal/bounded/partial outcomes separately.
- [x] Add a small `requests` client with bounded polling, terminal failure handling and atomic artifact downloads. Keep `/v1/jobs` and the current server. Verify using real local HTTP against an isolated test server, without touching production manifests.
- [x] Document commands, status/result/download semantics, prerequisites, authenticated forwarding for remote use and current serial/single-trust deployment limits.
- [x] Check release whitelist against Git tracking and source export; inventory generated/historical files without deleting evidence or account state.
- [x] Run the existing ten-module CI regression plus focused new tests and package validation; audit every requirement against actual evidence before reporting completion.

## Architecture decisions

Navigation repairs must use verified post context and semantic UI evidence, never this specific URL or a global comment-filter bypass. Target/source and terminal checks remain authoritative.

API delivery adds `facebook_api_client.py`, `test_facebook_api_client.py` and `API_USAGE.md`. It reuses existing routes and download artifacts; client timeouts never silently cancel or resubmit work. Future multi-user/multi-machine scheduling is explicitly outside this maintenance increment; remote access can forward the existing loopback service through an authenticated channel.

## Evidence policy

Existing test counts in older logs are historical, not fresh validation of this change. Fresh evidence is recorded below.

## Findings and verified increments

- Fresh failing baseline `fb_b5c178f6321f4944`: 155.477 seconds; target `1476320540987565` verified, reported count 162, no Reaction query. `readonly-ui/reaction-strict-detail-reveal-01.xml` contains a right-hand NAF summary `[964,752][1048,868]` beside the post Like/Comment/Share buttons. `Find-ReactionSummaryIconNode` rejects it solely because of its fixed `Y1 >= 900` floor. Comment reaction numbers below this row remain invalid candidates.
- Historical PDF review: the older `share/p/1GP4DP9A1z/` also resolved to this same target and had `query-not-triggered`; it is not an independent successful regression sample. Source references: `查看系统优化进度.pdf` pp.15–28, `Facebook点赞显示差异.pdf` pp.1–5,17,23–24,34, `查看桌面项目.pdf` pp.5–25.
- Positive live control `fb_8055b701e7294e5a`: `share/r/1Dg59X6ysJ/`, 120.834 seconds, 2 CSV users from only `122122050735339946`, one page, terminal seen, target matched, integrity complete, zero rate limits. New Python client retrieved status/result and downloaded CSV to `runs/stabilization-client-share-r/` using the production HTTP interface.
- Stable startup contained an unconditional 8-second sleep immediately before an already bounded framework readiness loop. Removed the fixed sleep. Executing the old/new block with controlled ready/unready responses gives ready-delay 8/0 seconds; both reject unready services. This is a deterministic saved delay, not a claim about total cold-boot speed.
- Progress detail correction: failed/queued/running/cancelled jobs no longer claim all artifacts were saved; completed text follows selected artifact readiness. Omitted MITM archives no longer claim archive verification. Focused progress tests 10 passed; API tests 104 passed.
- Distribution gap: STOP scripts were in `release-files.txt` but ignored by Git; the Session 0 launcher was absent from both. These scripts are now admitted explicitly, and `facebook_visible_emulator.py` replaces the launcher-specific hard-coded local CMD dependency with configuration-based resolution.

## Engineering inventory and preservation

Runtime: `api-jobs/`, `api-batches/`, `runs/`, `worker-bootstrap/` contain state/evidence; keep them local and do not reorganize while services run. `facebook.local.json`, `facebook_workers.json`, Android disks, credentials and certificates remain local.

Core source: root Python/PowerShell modules; `android-ui-dump-helper/` UI transport; `web/` and `web_v2/` presentation; `macos/` platform bootstrap; `tools/export_source.py` explicit source packaging. `release-files.txt` and default-deny `.gitignore` define deliverable scope.

Historical screenshots, ad-hoc probe scripts, root logs, old archives and handoff documents were inventoried by filename/date. Their future diagnostic use cannot be ruled out, so none is deleted or moved. Existing runtime logs may be refreshed by the normal launcher. Old fixed-hash UI tests and historical release-gate fixtures are not current capture evidence; do not rewrite their baselines simply to make them green.

All source modifications remain local; existing source rollback files are retained. New client/output files can be removed independently; tracked incremental hunks can be reviewed against the pre-task backup and Git baseline. No sessions, account state or historical run data was deleted.

### Files changed in this maintenance increment

- Reaction navigation and regression: `explore_facebook_readonly.ps1`, `test_facebook_capture_orchestrator.py`.
- Readiness wait: `start_facebook_stable.ps1`.
- API progress and integration: `facebook_capture_api.py`, `test_facebook_capture_api.py`, new `facebook_api_client.py`, new `test_facebook_api_client.py`, `requirements.txt`, new `API_USAGE.md`.
- Portable visible startup and release admission: `launch_visible_in_user_session.ps1`, new `facebook_visible_emulator.py`, new `test_facebook_visible_emulator.py`, `.gitignore`, `release-files.txt`. Existing `STOP.cmd` and `stop_all_facebook.ps1` are admitted to source delivery without redesigning shutdown.
- Documentation and CI: `README.md`, `CHANGELOG.md`, `.github/workflows/check.yml`, this execution record.

Pre-existing hunks in shared files remain alongside this work. In particular, unrelated changes in `facebook_transport.ps1`, `run_facebook_capture.ps1`, `start_api_and_emulator.ps1`, `test_ui_v2_and_safety.py` and `web_v2/index.html` are not attributed to this increment.

## Fresh real API acceptance

| Case | Job | Duration | Downloaded CSV | Scope and proof |
| --- | --- | --- | --- | --- |
| Reported target, `share/p/1Jetj5opcE/` | `fb_3ac18cdd470146e1` | 94.616 s | 167 unique users | terminal, 8 successful response pages (7 ALL plus a category response), count 167, unique source `1476320540987565`, integrity complete, zero rate limits |
| Historical Reel, `share/r/1Dg59X6ysJ/` | `fb_8055b701e7294e5a` | 120.834 s | 2 unique users | terminal, 1 page, unique source `122122050735339946`, integrity complete, zero rate limits; run before navigation change, unaffected alternate path |
| Historical share link to group, `share/p/1QXj6TbPNv/` | `fb_2af310cb5eed4238` | 97.232 s | 50 users | explicit `reaction_scroll_limit=1`, bounded-sample, 3 observed pages, source `1415834113749566`, target matched, partial-open-tail; not a full-list claim |

Client downloads and result JSON: `runs/stabilization-client-target/`, `runs/stabilization-client-share-r/`, `runs/stabilization-client-share-p/`. Downloads use HTTP artifact routes, not shared filesystem paths. The target's diagnostic action is `confirmed=true`, `after_state=reaction-list`, `entry_scan_scrolls=1`, `normalized_via_comments=false`; all 11 source checks matched. No target reopen or comment-list normalization was needed after the fix.

The 155.477 s failed baseline versus 94.616 s successful full run is an observed same-target improvement, not a controlled all-workload benchmark. Initial target verification timestamp moved from roughly 53.5 seconds after start to 38.8 seconds in the warm corrected run; app/network caching can affect that difference, so it is not attributed solely to selector changes.

## Regression and delivery evidence

- Existing eight non-API/non-orchestrator CI modules: 64 tests, 2 platform skips, no failures.
- Actual-function navigation fixtures plus orchestrator: 123 tests, no failures.
- API regression including progress detail: 104 tests, no failures.
- Client + visible emulator configuration/session tests: 23 tests, no failures (12 + 11).
- Total covered CI modules: 314 tests, 2 platform skips, no failures. Tests using mocked devices prove code contracts; real capture claims rely on the separate jobs above.
- Both frontend scripts pass Node syntax checks; changed PowerShell files pass Windows PowerShell parser checks. `pip check` reports no broken requirements.
- Explicit release files: 95; no release entry ignored by Git. Final archive `dist/facebook-source-20260915-165601.zip` (405308 bytes), SHA-256 `3d7fa3a25285233adc29a746ec486b83b98acb874dfb3ab8a98393c07f3d54e7`. ZIP integrity, exact member set, all manifest hashes and exact current release bytes passed independent verification.
- A fresh extraction of the preceding archive passed client/orchestrator/visible-emulator tests (143 tests). After the final Session 0 guard changes, the final archive was independently extracted again to `C:\Users\lenovo\AppData\Local\Temp\facebook-final-source-verify-vyjx8jue\facebook`; all 11 final visible-emulator tests passed there. Unchanged suites were not redundantly rerun.
- Final visible-startup review closed the Python entrypoint Session 0 reuse/start gap and made failed CIM queries explicit errors. Session 0 rejection is covered by isolated tests. Both real Python and PowerShell launcher entries returned reuse of PID 23256, Session 1, without restarting the emulator. The three substantial child tasks passed the persisted Luna Max runtime guard.

## Remaining boundaries

No GitHub push or visibility/license change was requested. Local source and the reviewed ZIP therefore contain changes not yet in the remote branch. Public/multi-tenant authentication, distributed workers and real macOS acceptance remain future work. This task preserves loopback binding and documents authenticated forwarding.

Historical UI fixed-hash tests are a snapshot of an older task and already fail against later legitimate changes; their constants were not falsified. Old release-gate tests tied to specific historical runs are not the portable CI gate. No broad cleanup deletion was justified.

The new visible launcher was verified for configuration resolution and live interactive-session reuse; fresh Session 0 token-based cold startup was not exercised. First-time CA provisioning still follows the existing explicit `start_facebook_stable.ps1 -WritableSystem` instructions; ordinary startup is not a replacement for provisioning. The final service check reports API ready, UI HTTP 200, worker idle, queue empty, capture lease available, and the primary Android device booted.
