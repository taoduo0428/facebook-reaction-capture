# Audit fix verification

Scope: the four findings from the read-only audit: emulator shutdown identity, English offline UI, strict-source pagination, and unconfirmed cancellation.

Backup: `backups/audit-fixes-20260915-220619/`. Existing dirty changes were retained. No production STOP, cancellation, new Facebook capture, or API restart was executed.

## Verification completed

| Scope | Command modules | Result |
| --- | --- | --- |
| Lifecycle | `test_facebook_transport_health test_facebook_shutdown_safety` | 12 passed |
| Navigation | `test_facebook_navigation_guards test_facebook_navigation_latency` | 11 passed |
| Capture/API contracts | `test_facebook_capture_api test_facebook_capture_orchestrator test_facebook_api_client` | 258 passed |
| Capture result safety | `test_facebook_batch_summary test_facebook_live_progress test_reaction_rate_limit_progress` | 29 passed |
| Environment/adapters | `test_facebook_local test_facebook_android_capture test_facebook_mac_device test_facebook_macos test_facebook_worker_bootstrap test_facebook_setup` | 52 run, 1 skipped |
| Network/reuse | `test_facebook_network test_facebook_warm_start test_facebook_unpin_reuse` | 20 run, 1 skipped |
| UI/safety | `test_ui_v2_and_safety` | 19 run, 18 passed, 1 pre-existing hash failure |

Commands use `rtk proxy C:\Windows\py.exe -m unittest` followed by the modules above. Fixtures isolate subprocess/device interactions. The skipped cases are opt-in real mitmdump integration and macOS-only fcntl contention.

The UI/safety failure predates these edits: expected SHA-256 for `web/index.html` is `89fe24d7e36144d35f76b3dc8657003259e5870f92916b49d3d15f9572008e17`; actual hash before and after is `74a0a765a0221fc7fe712396d3de2095dd0371e9a47a34e425315f5b78baf190`. The baseline was not changed to hide the discrepancy.

`facebook.local.json` and `facebook_workers.json` hashes remained unchanged. The live API health endpoint reported ready/idle, zero queued jobs and available capture lease; the UI returned HTTP 200.

## Validation limits

- API cancellation validation uses isolated managers, fake subprocesses and test HTTP servers. No production cancellation/STOP or real Facebook collection was used as a test.
- Shutdown identity helper is dynamically exercised with a fake ADB command; the unchanged full-script named-process fallback is reviewed statically.
- The original top-of-loop rate-limit refresh handling for a newly appearing session restriction was not expanded in this source-mismatch repair.
- The existing API process still has its previously loaded Python code. The patched API loads at the next normal service restart.
- `dist/facebook-source-20260915-232329.zip` is an intermediate pre-review snapshot, not the final cancellation-recovery delivery.

Final cancellation recovery is covered by the API suite above, including restored cancellation, multiple uncertain jobs, next-job claim, and cancellation during worker-failure diagnosis.
