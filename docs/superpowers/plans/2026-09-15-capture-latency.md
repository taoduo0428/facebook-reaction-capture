# Capture Latency Implementation Plan

**Goal:** Reduce demonstrated local startup/navigation overhead and avoid closing Facebook after every successful post without weakening post identity, comment exclusion, completion proof or throttling.

**Architecture:** Keep existing API, serial workers and per-job capture processes/files. Preserve normal-network restoration and failure recovery. Prefer checking already-ready local/UI state over unconditional restart/sleep. This is in-place maintenance of the user's active configured checkout; unrelated uncommitted edits are preserved.

**Tech Stack:** Windows PowerShell 5.1, Python unittest, existing localhost API/Android capture.

## Baseline and ownership

- Latest two-post batch: `batch_ef3f1ceaf8fb41da`, jobs `fb_f095cb4a6a6844eb` and `fb_6687452fdaf74164`. Read only these recent timing artifacts plus relevant code.
- `run_facebook_capture.ps1` calls `Confirm-FacebookUiOnline` after every successful capture. That helper currently initializes `forceRestartNext=true` and performs `-ForceStop` plus a fixed wait even for a healthy UI.
- Existing MITM startup sleeps three seconds before checking only whether its process exited.
- Fixed navigation waits must be distinguished from URL resolution, strict target verification, UI loading and pagination pacing before editing.
- Source rollback copies: `backups/latency-optimization-20260915/` (ten files, including CI). No service restart or account-state edits are part of implementation.

## Bounded implementation and verification

- [x] Health: healthy in-place check and retained bounded recovery; six actual-function cases passed.
- [x] MITM readiness experiment evaluated and withdrawn after live process-tree ownership incompatibility. Original startup block restored exactly from pre-task backup; failed-run evidence retained. No TCP optimization is delivered.
- [x] Navigation: Reaction click and strict detail reveal use early snapshots and the remaining original wall-clock budget; six targeted cases and 123 existing orchestrator cases passed. Open-Url's eight-second wait remains unchanged.
- [x] Injection reuse: confirmed dual-hook installation tied to boot ID, PID/start time and SHA-256, checked around UI readiness; missing evidence/changed identity/cold start retains original attach. Replacement attach invalidates the old claim first. Eight identity/cache tests and three warm-start cases passed.
- [x] Main integration: reviewed diffs and persisted Luna Max guards. Final focused regression: 23 tests plus 123 orchestrator tests, no failures.
- [x] Serial live batch `batch_09f57094a0b94460`: both jobs terminal, same Facebook PID1839, second job reused hooks, zero rate-limit/source-mismatch signals. First result185/186 is accurately labeled terminal-visible-subset; second913/913 complete.
- [x] Evidence and timings recorded in `evidence/latency-20260915/README.md`; release/CI updated and 98-file source package verified. No Git push or deletion of historical data.

The skill's generic worktree/commit/extra-agent review ceremony is intentionally narrowed to the user's configured live checkout and existing explicit Astra/Luna ownership. The user has already authorized implementation; no extra approval round is needed. Short live acceptance cannot prove that Facebook will never limit an account. Request pacing, rate-limit backoff and concurrency remain unchanged.
