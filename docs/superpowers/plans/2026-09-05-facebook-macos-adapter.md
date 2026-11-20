# Facebook macOS Worker Adapter Implementation Plan

## 2026-09-09 当前核对

适配层、CLI 模拟、架构选择、端口隔离和文档已完成；Windows 模拟与 CI 不能证明真实 M4 的 Hypervisor、ARM64 镜像、Keychain/CA、Frida、mitmproxy、Facebook 登录或深度抓取。该边界保持不变。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add an isolated macOS bootstrap and transport adapter for two Android emulator workers without changing the existing Windows worker registry or runtime.

**Architecture:** Keep all macOS code and configuration under `macos/`. The adapter reads a Mac-only registry, validates global identity and port isolation, emits deterministic emulator/ADB commands in simulation mode, and executes only those commands in real mode. Existing Windows PowerShell, `facebook_workers.json`, API scheduling, sessions, and account state remain untouched.

**Tech Stack:** Python 3 standard library, Android SDK `emulator` and `adb`, POSIX shell, `unittest`.

---

### Task 1: Define the isolated worker registry

**Files:**
- Create: `macos/facebook_workers.macos.example.json`
- Test: `test_facebook_macos.py`

- [x] **Step 1: Write tests proving worker ids, serials, AVD names, capture ports, and native ports must be globally unique.**
- [x] **Step 2: Run `python -m unittest test_facebook_macos.py` and confirm the missing module fails.**
- [x] **Step 3: Implement typed config loading and validation without importing or writing `facebook_workers.json`.**
- [x] **Step 4: Add two enabled sample workers plus a disabled third worker, with no account or credential material.**

### Task 2: Implement deterministic simulation and environment diagnosis

**Files:**
- Create: `macos/facebook_macos.py`
- Test: `test_facebook_macos.py`

- [x] **Step 1: Test Apple Silicon and Intel ABI selection, emulator command generation, and ADB reverse/proxy command generation.**
- [x] **Step 2: Test that simulation returns structured JSON and never invokes a subprocess.**
- [x] **Step 3: Implement `doctor`, `simulate`, `start`, and `transport` commands with bounded subprocess timeouts.**
- [x] **Step 4: Reject disabled workers and report missing Android SDK tools without exposing environment secrets.**

### Task 3: Add the Mac operator entry point and documentation

**Files:**
- Create: `macos/start_facebook_workers.sh`
- Create: `macos/README.md`

- [x] **Step 1: Add a POSIX shell wrapper that resolves its own directory and invokes the Python adapter.**
- [x] **Step 2: Document Android SDK setup for Apple Silicon and Intel, AVD creation, manual Facebook login, transport checks, and real-machine acceptance commands.**
- [x] **Step 3: State that Windows simulation validates logic only, not macOS Hypervisor, Keychain, certificates, Frida, mitmproxy, Facebook login, or deep capture.**

### Task 4: Verify isolation and regressions

**Files:**
- Test: `test_facebook_macos.py`
- Inspect: `facebook_workers.json`
- Inspect: current ADB/API runtime

- [x] **Step 1: Run the new unit suite and CLI simulation for both enabled workers.**
- [x] **Step 2: Parse the shell entry point with Bash when available.**
- [x] **Step 3: Re-run the existing orchestrator, API, and worker bootstrap test suites.**
- [x] **Step 4: Recheck Windows worker serials, reverse mappings, worker-03 disabled state, and API health.**
- [x] **Step 5: Report implemented/simulated, implemented/not externally verified, and not implemented as separate result classes.**
