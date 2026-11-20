# Documentation and CI Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:verification-before-completion to verify each contract before reporting completion.

**Goal:** Align startup, shutdown, macOS prerequisite, API recovery, CI coverage, and source-export documentation with the current implementation.

**Architecture:** Keep runtime behavior unchanged except where a documented contract is currently misleading. Use existing entrypoints and class-qualified unittest selectors, and remove only the duplicate release manifest entry.

**Tech Stack:** Markdown, YAML GitHub Actions, PowerShell/POSIX command examples, Python unittest, `tools/export_source.py`.

---

### Task 1: Align user-facing startup and API instructions

**Files:**
- Modify: `README.md`
- Modify: `API_USAGE.md`

- [ ] State that Windows one-click startup prints the V2 URL and that `STOP.cmd` stops the hidden API/UI runtime; reserve Ctrl+C for the foreground macOS API.
- [ ] Document Windows and macOS API startup commands separately, with the configured `facebook.local.json` port as the source of truth.

### Task 2: Align macOS deployment runbook

**Files:**
- Modify: `macos/CODEX_DEPLOYMENT_RUNBOOK.md`
- Modify: `macos/QUICKSTART.md`

- [ ] Describe Build-Tools 35.0.0 as required only when building the optional UI Helper, while keeping the automatically installed SDK package list accurate.
- [ ] Replace masked `prepare_device.py ... || true` examples with captured exit status plus explicit JSON checks.
- [ ] Describe queued/running restart recovery using the actual requeue and takeover rules.
- [ ] Keep Python 3.12/3.13 as the tested versions and state that the implementation minimum is 3.12.

### Task 3: Make CI and release export contracts executable

**Files:**
- Modify: `.github/workflows/check.yml`
- Modify: `release-files.txt`

- [ ] Run the three passing UI/Windows contract test classes in both CI matrix environments without enabling the stale historical hash test.
- [ ] Remove the duplicate `CHECK.cmd` release entry so `tools/export_source.py` can build the archive.

### Task 4: Verify the repaired contracts

**Files:**
- Test: existing class-qualified unittest suites and `tools/export_source.py`

- [ ] Run the UI contract classes, focused API/orchestrator tests, source export, `git diff --check`, and a release-manifest duplicate check.
- [ ] Inspect the final diff and report real-device/M4 acceptance as unverified.
