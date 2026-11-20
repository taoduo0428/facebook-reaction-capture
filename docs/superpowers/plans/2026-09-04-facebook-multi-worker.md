# Facebook Multi-Worker Implementation Plan

## 2026-09-09 当前核对

Worker 注册、端口/serial 管线、隔离和管理脚本已完成；当前文档不新增双 Worker 深度并发成功结论。每个任务仍固定一个 Worker，禁止因限流切换账号继续同一任务。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add isolated multi-emulator workers so each enabled Facebook account can process one post at a time without changing Worker-01 data or silently moving a rate-limited post to another account.

**Architecture:** Keep one central HTTP API and persisted job queue. Load a versioned worker registry, run one scheduler thread per enabled worker, bind a claimed job to that worker for its full lifetime, and pass the worker serial and unique host ports through every PowerShell layer. Worker-02 is created from the installed Android system image as a clean AVD and remains disabled until the user logs in and explicitly enables it.

**Tech Stack:** Python 3 standard library, Windows PowerShell 5.1, Android SDK emulator/adb/avdmanager, existing HTML/CSS/JavaScript console, unittest.

---

### Task 1: Worker Registry and Validation

**Files:**
- Create: `facebook_workers.json`
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] **Step 1: Write failing registry tests**

```python
def test_default_workers_preserve_primary_and_disable_secondary(self):
    workers = api.load_worker_config(path)
    self.assertEqual(workers[0].serial, "emulator-5554")
    self.assertEqual(workers[0].avd_name, "fb-x86_64-api35")
    self.assertTrue(workers[0].enabled)
    self.assertFalse(workers[1].enabled)

def test_worker_ports_and_identity_must_be_unique(self):
    with self.assertRaises(api.ApiError):
        api.validate_worker_config(duplicate_port_workers)
```

- [x] **Step 2: Run tests and confirm they fail because worker configuration APIs do not exist**

Run: `python -m unittest test_facebook_capture_api.WorkerConfigTests -v`

- [x] **Step 3: Add immutable worker records and strict validation**

Implement `WorkerConfig`, `load_worker_config()`, and uniqueness checks for `worker_id`, `serial`, `avd_name`, `capture_port`, and `native_port`. Validate port range and require at least one enabled worker.

- [x] **Step 4: Add the two-worker registry**

```json
{
  "version": 1,
  "workers": [
    {"worker_id":"worker-01","serial":"emulator-5554","avd_name":"fb-x86_64-api35","capture_port":8081,"native_port":18766,"enabled":true},
    {"worker_id":"worker-02","serial":"emulator-5556","avd_name":"fb-x86_64-api35-worker-02","capture_port":18082,"native_port":18767,"enabled":false}
  ]
}
```

- [x] **Step 5: Run registry tests and confirm they pass**

Run: `python -m unittest test_facebook_capture_api.WorkerConfigTests -v`

### Task 2: Dynamic Device and Port Plumbing

**Files:**
- Modify: `facebook_capture_api.py`
- Modify: `start_facebook_stable.ps1`
- Modify: `run_facebook_capture.ps1`
- Modify: `explore_facebook_readonly.ps1`
- Modify: `facebook_transport.ps1`
- Test: `test_facebook_capture_api.py`
- Test: `test_facebook_capture_orchestrator.py`

- [x] **Step 1: Write failing command-plumbing tests**

Assert `build_capture_command()` emits `-ProxyPort 18082` and `-ReactionNativeScrollPort 18767`, and `ensure_emulator()` invokes `start_facebook_stable.ps1 -Serial emulator-5556 -AvdName fb-x86_64-api35-worker-02 -CapturePort 18082 -NativePort 18767`.

- [x] **Step 2: Run the focused tests and confirm the missing arguments fail**

Run: `python -m unittest test_facebook_capture_api.WorkerCommandTests test_facebook_capture_orchestrator.py -v`

- [x] **Step 3: Thread ports through the Python and PowerShell boundaries**

Add `ReactionNativeScrollPort` parameters and pass the value into `Invoke-FacebookNativeReactionCheck` and `Invoke-FacebookNativeReactionScroll`. Add `CapturePort` and `NativePort` startup parameters so each serial receives only its own reverse/forward mappings.

- [x] **Step 4: Run focused tests and PowerShell parser validation**

Run: `python -m unittest test_facebook_capture_api.WorkerCommandTests test_facebook_capture_orchestrator.py -v`

### Task 3: Multi-Worker Scheduler and Persistent Ownership

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [x] **Step 1: Write failing scheduling tests**

Cover two enabled workers claiming different jobs concurrently, disabled workers claiming none, persisted `worker_id` and `serial`, cooldown exclusion, and a failed/rate-limited job remaining bound to its original worker.

- [x] **Step 2: Run scheduler tests and confirm single-worker behavior fails them**

Run: `python -m unittest test_facebook_capture_api.MultiWorkerManagerTests -v`

- [x] **Step 3: Implement one queue consumer per enabled worker**

Claim queued work atomically, set `worker_id` and `serial` before execution, retain ownership in manifests, and never requeue a running or failed job on a different worker. Disabled and cooling workers do not start queue consumers.

- [x] **Step 4: Expose per-worker health without account data**

Return `workers[]` with worker id, serial, AVD name, enabled state, busy/idle/disabled state, Android readiness, and ports. Keep legacy top-level health fields for the primary worker.

- [x] **Step 5: Run scheduler and persistence tests**

Run: `python -m unittest test_facebook_capture_api.MultiWorkerManagerTests -v`

### Task 4: Worker-02 Lifecycle Tooling

**Files:**
- Create: `manage_facebook_workers.ps1`
- Test: `test_facebook_capture_orchestrator.py`

- [x] **Step 1: Write static safety tests for the management script**

Require explicit `Create`, `Start`, `Status`, `Enable`, and `Disable` actions; verify clean creation uses `avdmanager create avd`; reject Worker-01 creation; and reject any copy operation from Worker-01 data.

- [x] **Step 2: Implement idempotent Worker-02 lifecycle commands**

`Create` builds only `fb-x86_64-api35-worker-02` from `system-images;android-35;google_apis;x86_64`. `Start` uses serial `emulator-5556`. `Enable` is allowed only after ADB sees the device and Facebook package data exists; it atomically updates only Worker-02's enabled field.

- [x] **Step 3: Run management-script tests and syntax validation**

Run: `python -m unittest test_facebook_capture_orchestrator.py -v`

### Task 5: Create and Start the Clean Secondary AVD

**Files:**
- Create externally: `D:\Android\AVDManual\fb-x86_64-api35-worker-02.avd`
- Preserve exactly: `D:\Android\AVDManual\fb-x86_64-api35.avd`

- [x] **Step 1: Record read-only baseline fingerprints for Worker-01**

Record first AVD directory metadata and selected config hashes without reading or copying account databases.

- [x] **Step 2: Create Worker-02 from the installed system image**

Run: `powershell -ExecutionPolicy Bypass -File .\manage_facebook_workers.ps1 -Action Create -WorkerId worker-02`

- [x] **Step 3: Start Worker-02 only and install project prerequisites to its serial**

Run: `powershell -ExecutionPolicy Bypass -File .\manage_facebook_workers.ps1 -Action Start -WorkerId worker-02`

- [x] **Step 4: Verify isolation and leave Worker-02 disabled for login**

Confirm ADB serial `emulator-5556`, AVD name, unique reverse/forward ports, Facebook login surface, and unchanged Worker-01 fingerprints. Do not enable Worker-02.

### Task 6: Full Regression and User Handoff

**Files:**
- Modify if required: `web/app.js`
- Modify if required: `web/index.html`

- [x] **Step 1: Show worker state in the existing console**

Render each worker as disabled, idle, busy, cooling, or offline without displaying account identity or session material.

- [x] **Step 2: Run all focused and full tests**

Run: `python -m unittest test_facebook_capture_api.py test_facebook_capture_orchestrator.py -v`

- [x] **Step 3: Run syntax checks**

Run Python compile, Node syntax, and PowerShell parser checks for every changed script.

- [x] **Step 4: Perform a local API health smoke test**

Confirm `/health` reports both workers, Worker-01 retains its original binding, and Worker-02 reports disabled until manual login and enablement.

- [x] **Step 5: Report the exact login and enable command**

Give the user the Worker-02 emulator window for login. After login, enable with `manage_facebook_workers.ps1 -Action Enable -WorkerId worker-02`; no queued post is migrated across workers.
