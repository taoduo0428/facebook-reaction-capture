# Facebook Batch Capture UI Implementation Plan

## 2026-09-09 当前核对

批量 API、顺序队列、聚合产物、去重、进度页和 fixture 验证已完成。真实多链接 Facebook 深度任务仍需逐帖满足目标匹配、终点和完整性条件；页面接受 URL 不等于每帖都可枚举。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Extend the existing loopback capture console from one post to a clear batch view with per-post progress, per-post artifacts, summed valid users, and cross-post deduplicated users.

**Architecture:** Keep the existing single `JobManager` worker so one Android emulator processes posts sequentially. Add a persisted batch manifest that owns ordered child jobs, generate aggregate CSV/JSON files atomically from completed child CSV files, and let the browser select one child row for the existing eight-stage and artifact detail views.

**Tech Stack:** Python 3 standard library, PowerShell/ADB capture worker, vanilla HTML/CSS/JavaScript, `unittest`, loopback HTTP, browser accessibility and screenshot verification.

---

### Task 1: Batch request and persistence model

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] Add tests that normalize one-to-many Facebook URLs, reject duplicates and over-limit batches, persist `batch_id`, child order, and each child `job_id` without embedding result payloads.
- [x] Run `python -m unittest test_facebook_capture_api.py` and confirm the new tests fail before implementation.
- [x] Implement `normalize_batch_request`, atomic batch manifests under `api-batches`, and ordered child creation through the existing single worker queue.
- [x] Run the targeted tests and confirm batch persistence passes without changing legacy single-job behavior.

### Task 2: Batch aggregation and deduplication

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] Add fixture tests with two child CSV files containing one shared `user_id`, and require per-post sum `4`, duplicate overlap `1`, and batch unique count `3`.
- [x] Implement atomic `batch-posts.csv`, `batch-reactor-users.csv`, `batch-user-post-relations.csv`, and `batch-summary.json` generation after every terminal child state.
- [x] Preserve the first complete user row per `user_id`, write one relation per user/post pair, and never infer identities from names.
- [x] Run the aggregation tests and verify rerunning aggregation is idempotent.

### Task 3: Batch HTTP API and progress snapshots

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] Add HTTP tests for `POST /v1/batches`, `GET /v1/batches/latest`, and `GET /v1/batches/<id>/progress` including empty and unknown batches.
- [x] Return bounded batch totals, per-child status/stage/counts, aggregate artifact metadata, and the selected child job identifiers without returning user rows.
- [x] Keep existing `/v1/jobs` routes operational for prior tasks and direct artifact downloads.
- [x] Run all API tests and confirm no legacy route regressions.

### Task 4: Batch-first responsive UI

**Files:**
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`

- [x] Replace the single URL input with a multiline one-URL-per-line field while retaining the same save checkboxes.
- [x] Add a batch summary containing link total, completed, running, failed, per-post count sum, and cross-post unique count.
- [x] Add a compact per-post task table; selecting a row updates the existing eight-stage timeline, live metrics, run directory, and artifact actions.
- [x] Add aggregate result downloads and responsive narrow-screen task rows without nested cards or horizontal action loss.
- [x] Validate JavaScript syntax and confirm all dynamic text uses text nodes rather than HTML injection.

### Task 5: End-to-end verification

**Files:**
- Verify: `facebook_capture_api.py`
- Verify: `web/index.html`
- Verify: `web/styles.css`
- Verify: `web/app.js`
- Verify: `test_facebook_capture_api.py`

- [x] Run the complete API test file, Python bytecode compilation, and JavaScript syntax check with zero failures.
- [x] Start the updated service on `127.0.0.1:8766`, verify health and existing completed-task compatibility, and exercise a fixture-backed batch endpoint without launching a live Facebook capture.
- [x] Verify desktop and narrow browser screenshots show readable batch totals, task selection, process detail, and file actions without overlap.
- [x] Report deterministic verification separately from any future real multi-post emulator run.
