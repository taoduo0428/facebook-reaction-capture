# Facebook Worker Bootstrap and URL Support Plan

## 2026-09-09 当前核对

URL 矩阵和 Worker 创建代码已完成；worker-03 的人工登录、真实 Probe、API 任务和启用仍未形成新的验收证据，原未勾选项继续保留。最新 URL 类型证据见 `CURRENT_STATUS_2026-09-09.md`。

**Goal:** Document the exact Facebook URL support boundary and deliver a one-click, resumable workflow that creates a clean isolated emulator worker with all capture prerequisites configured.

**Safety boundary:** A new worker receives only application APKs and project tooling. Facebook account data, cookies, tokens, passwords, OTPs, and emulator user-data images are never copied. The worker remains disabled until the user completes login and the final health probe passes.

## Task 1: Evidence-backed URL matrix

- [x] Inventory URL parsing, resolution, strict-target validation, and tests.
- [x] Separate live-validated URL forms from code/test-only forms.
- [x] Record unsupported and visibility-limited cases.
- [x] Publish `FACEBOOK_LINK_SUPPORT.md` with evidence paths and dates.

## Task 2: Deterministic worker reservation

- [x] Add a Python helper for lock-protected, atomic worker reservation.
- [x] Allocate the next `worker-NN`, clean AVD name, unused even emulator serial, and globally unique free capture/native ports.
- [x] Persist a resumable bootstrap state without enabling the worker.
- [x] Unit test allocation, collisions, lock behavior, state transitions, and invalid registries.

## Task 3: One-click provisioning

- [x] Add `add_facebook_worker.cmd` as the double-click entry point.
- [x] Add `add_facebook_worker.ps1` with Add, Resume, Finalize, and Status actions.
- [x] Create and start a clean Android 15 Google APIs AVD.
- [x] Extract only Facebook split APKs and the Frida server binary from an online source worker, then install them on the new serial.
- [x] Install the UI dump helper and configure independent proxy/reverse/forward mappings.
- [x] Install and verify the mitmproxy CA in both the writable system store and the Android 15 Conscrypt runtime store; keep Frida SSL unpin enabled for Facebook pinning.
- [x] Open the Facebook login screen and leave the worker disabled.
- [x] Preserve failure evidence and support idempotent resume.

## Task 4: Runtime registration and finalization

- [x] Add a local-only API worker-registry reload endpoint.
- [x] Reject identity changes/removal of already loaded workers and preserve running-job ownership.
- [ ] On Finalize, verify boot, package, proxy, mappings, helper, CA, Frida, and logged-in UI.
- [ ] Run a bounded reaction health probe before enabling the worker.
- [ ] Enable the worker only after all checks pass.

## Task 5: Verification

- [x] Run focused Python unit tests.
- [x] Parse every changed PowerShell script and validate Python/CMD syntax contracts.
- [x] Run the existing capture API/orchestrator regression suite.
- [x] Create `worker-03` with the one-click workflow and verify AVD/config/process isolation.
- [ ] After manual login, complete a real low-frequency Probe and API capture on `worker-03`.
