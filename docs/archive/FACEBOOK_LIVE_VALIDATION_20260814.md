# Facebook Live Validation Report

Date: 2026-08-14
Workspace: `C:\Users\lenovo\Desktop\facebook`

## Environment

- Emulator: `emulator-5554`, AVD `fb-x86_64-api35`, Android API 35
- Facebook: `555.0.0.49.59`
- Normal proxy after capture: `10.0.2.2:7897`
- Temporary capture proxy: `10.0.2.2:8081 -> mitmproxy -> 127.0.0.1:7897`

## Network Repair

The failure was caused by the emulator having no active default network. Before repair, `dumpsys connectivity` reported `Active default network: none` and TCP to `10.0.2.2:7897` returned `Network is unreachable`.

The AVD was restarted with its actual AVD directory, `ANDROID_AVD_HOME=D:\Android\AVDManual`. After restart:

- `eth0` was `UP` with `10.0.2.15/24`.
- Android network `100` was validated with route `0.0.0.0/0 -> 10.0.2.2`.
- Ping to `10.0.2.2` succeeded.
- TCP probe to `10.0.2.2:7897` succeeded.

No Facebook data was cleared and no application package was replaced during this repair.

## Capture Verification

The repaired capture path produced valid, parseable, redacted JSONL:

| Run | Records | JSONL errors | GraphQL result | Parsed posts |
| --- | ---: | ---: | --- | ---: |
| `runs/20260814-154757` | 51 | 0 | 50 HTTP 200 | 4 |
| `runs/20260814-155107` | 74 | 0 | fresh feed plus more-data responses | 16 |

The `154757` analysis includes a public `Japan Inside` post:

- Post ID: `1355454476757467`
- Reactions: `4,095` in the captured response; the live UI later showed `4,108`.
- Shares: `319` in the captured response; the live UI later showed `320`.
- Comments: `231`
- Public text was extracted from the response and saved in `facebook-posts.csv`.

The live UI reaction page showed these visible users for that post:

`Rod Carlo David Agpalo`, `Thabiso Sinxezi`, `Graham Pierce`, `David Ian James`, `Gladys Dzata`, `MHaekal`, `Lease Consultancy`, `Candra Dwiyanto`, `Bao An Tran Xuan`, and `Sergi Corral Margodt`.

This is a visible sample, not a claim that all 4,108 reactors were returned.

## Code Fixes

- `facebook_capture_addon_safe.py`: exact sensitive-key matching, valid JSON-safe redaction, and repair for Facebook escaped URL terminators.
- `analyze_facebook_capture.py`: extraction of `PartialStory.feedback` post IDs, owners, counts, and nested messages.
- `test_analyze_facebook_capture.py`: eight regression tests covering reactor shapes, deduplication, redaction integrity, and nested feed metadata.

Verification: `python -m unittest -v` passed 8 tests; `python -m py_compile` passed.

## Share-User Boundary

The live UI opened the `320次分享` view and displayed reshare content, but this run did not emit a new `ResharesOfContentFeedQuery`; the view was served from cached/preloaded data or constrained by Facebook visibility. The previously verified run `runs/20260814-repo-verify-133332` contains 7 visible share users and is documented in `FACEBOOK_TASK_RESULT.md`.

The current account/UI also explicitly states that reaction names are limited to visible friends. Counts cannot be converted into a full user list. No request replay, cursor forgery, or privacy bypass was performed.

## Follow-up Validation

The capture workflow was rerun after fixing two UI automation defects:

- UI XML is now pulled from the emulator instead of being redirected through PowerShell, preserving UTF-8 Chinese labels and bounds.
- `Tap-First` now returns a real Boolean; a missing entry no longer enters the success branch because of a log string in the pipeline.

Evidence from the reruns:

- `runs/20260814-163611`: 101 redacted records, 17 parsed public posts, 0 JSONL errors.
- `runs/20260814-164334`: 94 redacted records, 9 parsed public posts, 0 JSONL errors.
- Both reruns contained normal GraphQL feed traffic but no new `FeedbackReactorsGraphService` or `ResharesOfContentFeedQuery` response. The visible page did not expose a clickable reaction/share-count node, so no new user list is claimed from these runs.
- `runs/20260814-170218`: 37 redacted records and 2 parsed posts after reloading a known public post URL while capture was active; the post body and current counts were captured, but no new reactor/reshare query was emitted.
- `runs/20260814-175237`: 75 redacted records, 4 parsed public posts, 4 streaming GraphQL responses, and 0 JSONL parse errors. The short run completed in about 41 seconds for a 35-second capture deadline and restored the proxy to `10.0.2.2:7897`.
- `runs/20260814-175901`: a 120-second manual-assisted window produced 41 redacted records and 8 parsed public posts. No `FeedbackReactorsGraphService` or `ResharesOfContentFeedQuery` appeared in that window; the temporary listener stopped and the proxy was restored to `10.0.2.2:7897`.

The capture runner also now validates ADB before reading the previous proxy and treats an empty proxy response safely, so a transient ADB protocol fault cannot leave the runner in a partially switched state.

The read-only UI helper now scans malformed-but-readable Facebook node tags without strict XML parsing. It only opens a share list from a clickable `次分享/shares` count or a reaction list from a clickable top-level numeric count, then performs bounded list scrolling. Action buttons such as `赞`, `评论`, and `分享` are never used as metric fallbacks.

The existing verified boundary remains: 125 visible reactor users and 7 visible reshare users from earlier runs. Those are observable account-scoped results, not full-count expansion.
