# Facebook Capture UI Implementation Plan

## 2026-09-09 当前核对

原始 PCAP 生命周期、脱敏进度、API/UI、取消和本地 fixture 验证已完成。完整端到端结论仍依赖真实 UI 启动的任务；历史 full smoke 的 MITM/PCAP 证据与近期首屏 Probe 分开记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a loopback-only web UI that starts an authorized Facebook Android Reaction capture from a post URL, shows sanitized live progress, and lists every evidence artifact from encrypted PCAP through parsed CSV.

**Architecture:** Reuse `facebook_capture_api.py` as the only job controller and `run_facebook_capture.ps1` as the only capture orchestrator. Add raw-network capture to the orchestrator, expose derived progress/artifact views through read-only API endpoints, and serve a dependency-free HTML/CSS/JavaScript UI from the same loopback origin.

**Tech Stack:** Python 3 standard library HTTP server, PowerShell/ADB/tcpdump, vanilla HTML/CSS/JavaScript, `unittest`, Playwright for visual verification.

---

### Task 1: Raw PCAP lifecycle

**Files:**
- Modify: `run_facebook_capture.ps1`
- Test: `test_facebook_capture_orchestrator.py`

- [x] Add a failing structural test that requires `EnableRawNetworkCapture`, `raw-network.pcap`, bounded tcpdump start, SIGINT stop, pull, and remote cleanup.
- [x] Run `python -m unittest test_facebook_capture_orchestrator.py` and confirm the new test fails.
- [x] Add the minimal start/stop helpers and call them before mitmproxy startup and from `finally`.
- [x] Run the PowerShell parser and the targeted test to confirm they pass.

### Task 2: Sanitized progress and artifact model

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] Add failing tests for a progress snapshot that omits post/user identifiers and reports user count, page count, terminal state, stage state, and artifact locations.
- [x] Run the targeted tests and confirm failure.
- [x] Implement bounded JSON reads, artifact metadata, synthesized stages, and safe progress output.
- [x] Run the targeted tests and confirm they pass.

### Task 3: HTTP and static UI routes

**Files:**
- Modify: `facebook_capture_api.py`
- Create: `web/index.html`
- Create: `web/styles.css`
- Create: `web/app.js`
- Test: `test_facebook_capture_api.py`

- [x] Add failing HTTP tests for `/`, static assets, and `/v1/jobs/<id>/progress`.
- [x] Implement an allowlisted static-file sender and progress route with `no-store` and security headers.
- [x] Build the URL form, emulator status, stage timeline, live counters, artifact table, cancel action, and empty/error/completed states.
- [x] Run HTTP tests and JavaScript syntax validation.

### Task 4: UI launch path

**Files:**
- Modify: `start_facebook_capture_api.ps1`
- Create: `start_facebook_capture_ui.ps1`

- [x] Add a browser-opening UI launcher that binds only to `127.0.0.1` and reuses the existing API entry point.
- [x] Parse both PowerShell scripts and verify the configured URL.

### Task 5: Verification

**Files:**
- Verify: all modified files

- [x] Run API, orchestrator, raw-capture, progress, analyzer, and release-gate unit tests.
- [x] Start the service on an unused loopback port and verify health, HTML, CSS, JavaScript, and a fixture-backed progress response.
- [x] Use Playwright at desktop and mobile viewports to verify nonblank rendering, stable layout, URL validation, and no overlap.
- [x] Report raw PCAP support as implemented but only externally proven after the next real UI-started capture; do not reuse the historical wrapper run as proof of the new integration.
