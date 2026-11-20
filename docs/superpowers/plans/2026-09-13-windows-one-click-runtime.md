# Windows One-Click Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one Windows CMD entry that safely starts or reuses the configured primary Android emulator and the capture API, after which the user can open the existing V2 page.

**Architecture:** A small PowerShell orchestrator reads `facebook.local.json` and `facebook_workers.json`, checks live ADB/API health, starts only missing components through existing scripts, and re-enables a failed worker only when no capture is running. `START_API_AND_EMULATOR.cmd` is the double-click entry; it does not submit jobs or restart healthy services.

**Tech Stack:** Windows CMD, Windows PowerShell 5.1, existing JSON configuration and HTTP health API, Python unittest static launcher tests.

---

### Task 1: Add launcher contract tests

**Files:**
- Modify: `test_ui_v2_and_safety.py`

- [x] Add assertions that the CMD delegates to the new PowerShell orchestrator and that the orchestrator reads both local JSON files, reuses healthy services, invokes `start_facebook_stable.ps1` only for an unavailable primary emulator, starts the API only when absent, and never submits or deletes a batch.
- [x] Run `C:\Windows\py.exe -m unittest -q test_ui_v2_and_safety` and confirm the new assertions fail before implementation.

### Task 2: Implement the idempotent launcher

**Files:**
- Create: `START_API_AND_EMULATOR.cmd`
- Create: `start_api_and_emulator.ps1`

- [x] Parse `facebook.local.json` and the first enabled worker in `facebook_workers.json`; fail with an actionable message when configuration is missing or invalid.
- [x] Query the configured API health endpoint. Preserve a healthy active capture. When the primary emulator is unavailable, call `start_facebook_stable.ps1` with its configured serial, AVD, capture port, and native port.
- [x] Start `start_facebook_capture_api.ps1` in a hidden process only when the API is absent, wait for `/health`, and use the existing worker enable endpoint to clear a stale failed state only when no job is running.
- [x] Start or reuse the V2 server on port 8766 and print its URL without submitting work or changing batch data.

### Task 3: Verify without disturbing the live batch

**Files:**
- Test: `test_ui_v2_and_safety.py`

- [x] Run the focused unittest and PowerShell parser checks without executing the launcher.
- [x] Run `node --check web_v2/app.js` and `git diff --check`.
- [x] Inspect the diff to confirm no unrelated working-tree files were modified.
