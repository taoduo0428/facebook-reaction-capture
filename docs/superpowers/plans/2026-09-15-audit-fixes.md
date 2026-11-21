# Audit findings repair plan

**Goal:** Repair the four confirmed audit findings while preserving capture, pagination, cancellation, shutdown and existing account/runtime state.

**Architecture:** Keep existing entrypoints and result contracts. Validate emulator identity immediately before shutdown; stop pagination when target evidence is unsafe; recognize explicit offline screens; distinguish cancellation requested from confirmed process termination throughout API consumers.

**Tech Stack:** Python unittest, PowerShell AST-isolated fixtures, existing HTTP API and UI.

The existing dirty checkout contains user work. Keep its changes and make a rollback snapshot before edits. No production cancellation, emulator shutdown, service restart or new Facebook capture during verification.

## Tasks

- [x] Lifecycle: update `stop_all_facebook.ps1` to require successful matching AVD identity before `emu kill`; preserve project-specific fallback cleanup. Add isolated tests covering matching, foreign and unverified devices. Extend `facebook_transport.ps1` offline recognition with English strings, preserving Chinese recovery and the healthy warm path; extend `test_facebook_transport_health.py`.
- [x] Navigation: in `explore_facebook_readonly.ps1`, stop strict-target pagination when initial progress is unavailable and immediately stop after a confirmed mismatch during initial cooldown. Check other refreshed-progress branches before the next gesture. Preserve non-strict legacy behavior and rate-limit backoff. Add actual-function fixtures in `test_facebook_navigation_latency.py` or a separate focused test module.
- [x] Cancellation: in `facebook_capture_api.py`, persist a nonterminal cancellation state until termination is verified; retain the protective lease while process state is uncertain. Cover queued cancellation, successful running cancellation, unconfirmed cancellation, concurrent worker completion, repeat cancellation and recovery. Update batch/client/UI consumers where needed, without changing successful result/download behavior.
- [x] Integration: review diffs against the pre-task snapshot; run targeted regression suites and API/orchestrator/client/safety tests in isolated fixtures. Check the live health endpoint and UI read-only. Record actual test outcomes and activation limits in CHANGELOG.

For each task, first add a failing regression test, confirm its relevant failure, make the minimal implementation change, then run the targeted suite. Commands use `rtk proxy C:\Windows\py.exe -m unittest ...` and do not drive the production device.

Failure coverage: missing/mismatched emulator identity; explicit offline UI; absent or conflicting target progress; subprocess termination uncertainty and races. Preserve partial data, bounded retry/backoff, sequential worker ownership and all existing credentials.
