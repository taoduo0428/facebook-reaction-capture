# Portable Facebook Capture Implementation Plan

## 2026-09-09 当前核对

网络模式、Windows 热启动、Mac Python 执行器、共享 API/启动器、发布文档和本地测试已落地。新 URL 类型已有移动端 `initial-ready` 证据，但 9 月 8 日至 9 日 Probe 均未分页；真实 M4 全链路和逐类终点分页仍待验收。浏览器样本属于已记录的受限观察，不等于导出登录数据。

> **For agentic workers:** Use superpowers:subagent-driven-development. Follow the file ownership boundaries below; preserve account state and do not push until integration checks pass.

**Goal:** A Mac recipient can configure and run the local URL capture console without Clash, choose nonconflicting ports, and use browser-verified sample URLs.

**Architecture:** Keep the existing Windows capture engine. Add a Python Android capture engine for macOS using the existing parser, capture addon and strict source validation; dispatch by host platform through the common API. A shared network configuration distinguishes direct internet from an optional upstream HTTP proxy. Installation and diagnostics must distinguish host readiness from device acceptance.

**Tech Stack:** Python 3.12+, mitmproxy, Frida, Android SDK/ADB, Windows PowerShell, macOS shell, existing HTML/JS console.

## Requirements and verification

- [ ] Direct network and configurable upstream: `facebook_network.py`, Windows transport/capture/start scripts. Existing installations retain their explicit proxy choice. New direct configurations never require port 7897. Test both modes, malformed endpoints and conflicting ports; run a local HTTP origin through real mitmdump in regular mode.
- [ ] Mac runner: `facebook_android_capture.py` and focused tests. Implement bounded ADB commands, device/framework readiness, per-process lifecycle, CA/Frida prerequisites, target navigation, Reaction list opening, paging, strict target validation and shared output format. Missing login, unsupported UI, throttling, absent terminal and cancellation must never become successful completion. Replay real historical capture events through parser/pagination tests; simulated ADB is explicitly labelled.
- [ ] Shared API and launcher: `facebook_capture_api.py`, `facebook_local.py`, `START.command`, `setup.sh`, config templates. Dispatch macOS to Python runner, resolve macOS SDK/venv binaries, initialize collision-free worker ports without overwriting existing configs, expose setup diagnostics and actionable errors in the page. Check subprocess cancellation and completed output contract.
- [ ] Browser reconnaissance: use only the specified connected Chrome profile; open representative accessible public posts, verify the Reaction list in UI and record canonical URLs and observation dates. No likes/messages or login-data export. Await Chrome connection while implementation progresses.
- [ ] Documentation and delivery: update README, macOS guide, release whitelist, CI matrix and CHANGELOG. Explain Apple Silicon versus Intel, direct internet versus upstream proxy, installation/login/certificate steps and actual URL support. Run Windows and macOS CI, clean-source tests and source-boundary review, then push the integrated branch/main as appropriate.

## Failure map

Missing dependencies or SDK: doctor exits with concrete setup steps before capture. Occupied ports: never terminate unknown processes. Login/device challenge: stop with manual action required. Rate limits: bounded cooldown and partial output. No source identity or ambiguous post: reject as unverified target. No pagination terminal: preserve partial data and report incomplete. Multiple workers: isolate serial, ports, run directory and cancellation. Mac simulation does not prove real Hypervisor/CA/Frida/Facebook acceptance.

## Evidence ledger

- Baseline Git commit: ed63cd6623097b0774e61d3a18910de0bed6e079.
- Existing Mac adapter reports not-implemented-for-macos for deep capture.
- mitmproxy official mode docs: https://docs.mitmproxy.org/stable/concepts/modes — regular connects directly; upstream forwards through another proxy.
- Browser inventory currently exposes Edge and in-app browser; requested Chrome profile not yet connected.
