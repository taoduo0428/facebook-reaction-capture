# Facebook Two-Worker Content Matrix Implementation Plan

## 2026-09-09 当前核对

内容矩阵、Worker 隔离和已有 Probe 证据已完成到计划范围；当前新增 URL 运行仍是单 Worker、低压 `initial-ready`，不是双 Worker 并发全量验收。平台限流、权限和当前账号可见性仍按每帖单独判定。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `worker-01` and `worker-02` independently usable for real Facebook capture and prove representative normal/text-link, `story.php`, and Reel/video paths with strict target evidence.

**Architecture:** Keep `worker-03` disabled and untouched. Validate each active worker through its own emulator serial, capture port, native port, proxy mappings, CA/Frida path, and output directory; start with bounded Probe runs, then run API captures and a two-job concurrency isolation check. A URL is accepted only when fresh capture data strictly matches the requested source post.

**Tech Stack:** Windows PowerShell 5.1, Android Emulator/ADB, Frida, mitmproxy, Python `unittest`, local capture API on `127.0.0.1:8765`.

---

### Task 1: Freeze scope and establish the sample matrix

**Files:**
- Modify: `FACEBOOK_LINK_SUPPORT.md`
- Evidence: `facebook_workers.json`

- [x] **Step 1: Keep `worker-03` disabled and do not copy or automate Facebook account state.**
- [x] **Step 2: Select one normal/text-link post, one `story.php` post, and one Reel/video URL from prior strict-target evidence.**
- [x] **Step 3: Record content classification only from fresh UI/capture evidence; do not infer it from URL shape alone.**

### Task 2: Restore and qualify `worker-01`

**Files:**
- Inspect: `start_facebook_stable.ps1`
- Inspect: `facebook_certificate.ps1`
- Evidence: fresh `runs/<timestamp>/` directories

- [x] **Step 1: Start `fb-x86_64-api35` as `emulator-5554` with capture port `8081`, native port `18766`, and restored CA runtime binding.**
- [x] **Step 2: Verify boot completion, Facebook package, logged-in UI, proxy/reverse mappings, UI helper, certificate, and Frida/capture prerequisites.**
- [x] **Step 3: Run bounded strict-target Probes for the selected normal/text-link and Reel/video samples.**
- [x] **Step 4: Require target match, non-restricted session, zero rate limits, no unintended pagination, and usable Reaction semantics.**

### Task 3: Requalify `worker-02`

**Files:**
- Evidence: fresh `runs/<timestamp>/` directories

- [x] **Step 1: Recover the system-level ANR with an isolated Android reboot.**
- [x] **Step 2: Prove the `story.php` sample with a bounded Probe and strict source-post match.**
- [x] **Step 3: Run fresh bounded Probes for the selected normal/text-link and Reel/video samples.**
- [x] **Step 4: Require target match, non-restricted session, zero rate limits, no unintended pagination, and usable Reaction semantics.**

### Task 4: API and isolation acceptance

**Files:**
- Inspect: `facebook_capture_api.py`
- Evidence: `api-jobs/*.job.json`
- Evidence: fresh `runs/api-*/` directories

- [x] **Step 1: Submit real low-frequency API jobs only after both workers pass Probe.**
- [x] **Step 2: Verify each completed job records the assigned worker, strict target match, Reaction result or precise visibility limitation, terminal/open-tail semantics, and zero rate limits.**
- [x] **Step 3: Submit two bounded jobs concurrently and prove distinct worker ids, serials, ports, run directories, and no cross-target records.**
- [x] **Step 4: Confirm `worker-03` remains disabled throughout the concurrency run.**

### Task 5: Regression and documentation

**Files:**
- Modify: `FACEBOOK_LINK_SUPPORT.md`
- Modify if required by an observed defect: `run_facebook_capture.ps1`
- Modify if required by an observed defect: `open_facebook_post_readonly.ps1`
- Modify if required by an observed defect: `facebook_capture_api.py`
- Test if code changes: corresponding `test_*.py`

- [x] **Step 1: For each observed code defect, write a focused failing test before implementing the minimal fix.**
- [x] **Step 2: Run the relevant Python unit suites and PowerShell parser checks.**
- [x] **Step 3: Update the support matrix with fresh worker/content evidence and preserve unsupported/private/deleted/hidden-user boundaries.**
- [x] **Step 4: Audit every checkbox against current files and runtime artifacts before declaring completion.**

### Acceptance evidence

- `runs/20260905-105525`: worker-02 Reel Probe，严格 source post `122122050735339946`，1 个可见 Reaction 用户，零限流、终态完成。
- `runs/20260905-105830`: worker-02 `story.php` Probe，严格 post `122106880826437311`，31 个可见 Reaction 用户，零限流。
- `runs/20260905-110052`: worker-02 文字/链接 Probe，严格 post `1457763433066371`，正文与计数可读；Reaction 终态空 edges，分类为当前账号不可枚举。
- `runs/20260905-110342` 与 `runs/20260905-110513`: worker-01 分别完成 Reel 与 `story.php` Probe，source post 严格匹配，零限流。
- `api-jobs/fb_ede87700577a475b.job.json` 与 `api-jobs/fb_a569eae0dfdf411e.job.json`: 两个并发 API 任务均为 `completed`，分别绑定 worker-02/5556 和 worker-01/5554；对应分析结果只含各自目标 source post。
- 运行态复核：worker-01 reverse 为 `7897 + 8081`，worker-02 为 `7897 + 18082`；worker-03 为 disabled/offline。API 重启后能加载两个历史完成结果。
- 回归验证：`test_facebook_capture_orchestrator.py` 104 项、`test_facebook_capture_api.py` 60 项、`test_facebook_worker_bootstrap.py` 6 项通过；7 个相关 PowerShell 文件解析通过，`facebook_ssl_unpin.js` 通过 Node 语法检查。
