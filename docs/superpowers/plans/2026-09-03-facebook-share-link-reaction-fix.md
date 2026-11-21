# Facebook Share Link Reaction Capture Fix Implementation Plan

## 2026-09-09 当前核对

现有代码已覆盖严格目标绑定、`share/p`、`share/r`、Reel 与 Watch 归一化，并有对应单元测试和首屏 Probe。原计划中的“完整四链接 terminal 任务”不以首屏 `initial-ready` 代替，历史未勾选项按新的真实运行证据重新验收。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batch reaction capture resolve and verify every submitted Facebook `/share/p/` and `/share/r/` URL before opening its reaction-user list.

**Architecture:** Keep the strict post-isolation boundary. Extend safe capture with bounded, non-sensitive navigation metadata, then correlate Facebook 555 URL-resolver, static-story, and reels responses to one exact numeric post/author pair. Batch jobs remain isolated and produce a result for every submitted URL.

**Tech Stack:** Python 3, mitmproxy addon, PowerShell capture orchestration, `unittest`.

---

### Task 1: Add Facebook 555 target-verification regression tests

**Files:**
- Modify: `test_analyze_facebook_capture.py`
- Modify: `test_facebook_capture_api.py`

- [ ] Add fixtures for `GetURLResolverInfo` followed by `StaticGraphQlStoryFeedbackAtConnectionQuery`, and assert the Relay Story ID resolves to the expected numeric post and author.
- [ ] Add a `/share/r/` fixture for `ReelsVDDChainingQuery`, and assert only the first freshly opened reel's container Story is accepted.
- [ ] Add rejection tests for mismatched resolver/detail Relay IDs, malformed Relay IDs, and unrelated feed responses.
- [ ] Add a batch-worker test proving all submitted URLs receive terminal job manifests even when an earlier URL fails.
- [ ] Run the focused tests and confirm they fail for the missing behavior.

### Task 2: Implement strict Facebook 555 share-link correlation

**Files:**
- Modify: `facebook_capture_addon_safe.py`
- Modify: `analyze_facebook_capture.py`

- [ ] Persist only bounded public navigation metadata needed for correlation, without cookies, tokens, raw variables, or cursors.
- [ ] Decode Facebook Relay Story IDs defensively and extract one exact `(post_id, author_id)` pair.
- [ ] Require resolver/detail ID agreement for `/share/p/` and require a fresh first-edge reel container Story for `/share/r/`.
- [ ] Preserve existing search-result rejection and exact `source_post_id` isolation.
- [ ] Run focused analyzer and addon tests until green.

### Task 3: Verify existing batch execution reports every submitted post

**Files:**
- Inspect: `facebook_capture_api.py`

- [x] Confirm each capture exception is persisted per job and the worker always advances its queue in `finally`.
- [x] Confirm batch status is finalized only after every job is terminal.
- [x] Keep this code unchanged because it already satisfies the requirement.

### Task 4: Verify the four supplied URLs end to end

**Files:**
- Runtime outputs only: `api-jobs/`, `api-batches/`, `runs/`

- [ ] Run the full local unit-test suite and syntax checks.
- [ ] Restart only the project API/UI processes required to load the change.
- [ ] Submit the four supplied URLs as one batch and wait until all four jobs are terminal.
- [ ] Verify each job opened the intended numeric post, attempted the reaction list, and reports its own user/page count or a precise external visibility block.
- [ ] Record exact evidence paths and remaining external limitations.
