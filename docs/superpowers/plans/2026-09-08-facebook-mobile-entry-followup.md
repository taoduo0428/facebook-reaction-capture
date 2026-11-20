# Facebook Mobile Entry Follow-up Implementation Plan

## 2026-09-09 当前核对

Watch URL 归一化、目标边界和移动端入口修复已完成。最新 ATCTPE、群组多图、群组 permalink、Reel、Watch 与第二个 `pfbid` 运行均为 `initial-ready`；未分页到终点。后续若要宣称某类 URL 全量完成，仍需新的 `terminal-complete` 证据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Watch video URLs use the same verified mobile detail and Reaction-entry path as their canonical Reel URL, while preserving strict target matching.

**Architecture:** Normalize only Facebook `watch/?v=<numeric id>` inputs to the already supported `/reel/<id>/` mobile detail route. Keep the original user URL in diagnostics, pass the canonical route to Android and target verification, and add a source-level regression test before the live Probe.

**Tech Stack:** Windows PowerShell, Python `unittest`, Android ADB/Frida/mitm capture.

---

### Task 1: Lock the Watch URL regression

**Files:**
- Modify: `test_facebook_capture_orchestrator.py`

- [x] Add assertions that Watch query URLs are accepted as target-specific inputs and normalized before `Open-Url`.
- [x] Run the focused test and confirm it fails before implementation.

### Task 2: Normalize Watch URLs at the mobile target boundary

**Files:**
- Modify: `open_facebook_post_readonly.ps1`

- [x] Extend the target-specific URL check for `/watch/?v=<numeric id>`.
- [x] Extend `Convert-ToFacebookIntentUrl` to return `/reel/<numeric id>/` for that exact Facebook URL shape.
- [x] Preserve strict analyzer verification against the normalized canonical URL.

### Task 3: Verify live behavior and evidence

**Files:**
- Modify: `FACEBOOK_BROWSER_SAMPLES.md`

- [x] Run focused unit tests, PowerShell parse checks, and the original Watch URL with ProbeOnly.
- [x] Record the fresh run path and distinguish target normalization from full pagination completion.
- [x] Confirm the worktree and remote commit state before reporting.

### Follow-up reliability fix

- The health probe now fails closed when `reaction_screening` is absent or not `initial-ready`, and prints the latest action `failure_reason`.
- The second `pfbid` sample was re-tested with the same session and remained `not-ready`; the run was stopped before pagination.
