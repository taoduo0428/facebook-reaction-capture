# Bounded Reaction Sample Implementation Plan

**Goal:** Allow an explicitly requested, integrity-checked reaction sample stopped by its configured absolute cap to complete as a bounded sample while preserving terminal capture semantics.

**Architecture:** Keep the analyzer's `partial-open-tail` and `terminal_page_seen=false` evidence unchanged. Add a request explicitness flag and derive a separate bounded-sample completion scope from attempt diagnostics, strict target/source identity, valid user rows, error counters, and required artifacts. Persist the scope on jobs and expose it in batch progress and the v2 UI.

**Tech Stack:** Python `unittest`, JSON/CSV manifests, vanilla JavaScript/CSS.

---

### Task 1: Capture request intent and bounded evidence

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [ ] Preserve whether `reaction_scroll_limit` was present in the original payload while keeping the normalized integer value.
- [ ] Read optional attempt diagnostics from `readonly-ui/exploration-diagnostics.json` and permit bounded completion only for explicit limits, `absolute-cap`, matched target/source, positive valid users, zero rate/API/parse errors, and available required artifacts.
- [ ] Return `completion_scope="bounded-sample"` with `terminal_page_seen=false` and `completion_claim_allowed=false`; reject the default request and every failed evidence condition.

### Task 2: Persist completion scope through jobs and batches

**Files:**
- Modify: `facebook_capture_api.py`
- Test: `test_facebook_capture_api.py`

- [ ] Store the scope in job manifests, public job payloads, lazy results, and batch summaries.
- [ ] Mark a bounded result `completed` so the queue proceeds, and label batch post progress as a non-full sample.

### Task 3: Render the scope in v2

**Files:**
- Modify: `web_v2/index.html`
- Modify: `web_v2/app.js`
- Modify: `web_v2/styles.css`

- [ ] Display bounded completion as “样本完成（非全量）” and keep terminal indicators distinct from full completion.

### Task 4: Verify

**Files:**
- Test: `test_facebook_capture_api.py`

- [ ] Run bounded-valid, empty-user, target-mismatch, rate-limit, non-cap-stop, and terminal-complete regression tests.
- [ ] Run `rtk py -3 -m unittest -q test_facebook_capture_api.py test_facebook_post_queue.py`, Python compilation, Node syntax check, and `rtk git diff --check`.
