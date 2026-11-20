# Facebook Local Project Memory

> Historical snapshot. For the verified project state as of 2026-09-09, read `CURRENT_STATUS_2026-09-09.md` first. Older “current” statements below apply only to their recorded date.

Updated: 2026-08-15
Scope: C:/Users/lenovo/Desktop/facebook

## Verified Share-User Entry

- A recent public South China Morning Post post titled `Chinese tourist jailed in Singapore for burgling luxury homes on first visit` exposed the legacy comment/detail layout.
- In the feed post card, tap the `评论` action first. Evidence: `pfbid-post.xml`, node bounds `[190,1298][361,1414]`.
- In the resulting comment/detail layer, the top row exposes an interaction button and a separate clickable share-count button. Evidence: `pfbid-comments.xml`:
  - interaction count `116`, bounds `[21,95][240,190]`
  - share count `8次分享`, bounds `[909,106][1080,179]`
- The share-user request is triggered by tapping the share-count button center, approximately `(995,142)`. Do not tap the generic `分享按钮`; that opens the composer/action layer.
- New live capture evidence: `runs/20260815-025353/safe-facebook-http-capture.jsonl` contains one HTTP 200 `ResharesOfContentFeedQuery` response and one `FBResharesOfContentPinnedContentAttachmentQuery` response.
- The response identifies source post `1457763433066371`, original owner `100064981830080`, and returns three visible actors with `has_next_page=false`:
  - `Ruedee Pavasiriporn` (`100002479694500`)
  - `Mastura Dira` (`100002704356402`)
  - `Sommapa Atku` (`100001389831058`)
- Export: `runs/20260815-025353/analysis/facebook-share-users.csv`.

## Startup And Capture Index

- AVD: `fb-x86_64-api35`, Android 15/API 35, ADB serial `emulator-5554`.
- AVD home: `D:\Android\AVDManual`; emulator: `D:\Android\SdkManual\emulator\emulator.exe`.
- Normal guest proxy: `127.0.0.1:7897` through `adb reverse tcp:7897 tcp:7897` to host Clash. Temporary capture proxy: `127.0.0.1:8081` through `adb reverse tcp:8081 tcp:8081` to mitmproxy, which uses host Clash on `127.0.0.1:7897` upstream.
- Start with `C:\Users\lenovo\Desktop\facebook\start_facebook_stable.ps1`. The stable launcher deliberately starts the AVD without an emulator-level `-http-proxy`; as soon as ADB appears it installs reverse mappings before Android finishes booting, then validates the default network and Facebook UI.
- If Facebook is already showing posts, run `run_facebook_capture.ps1 -DurationSeconds 55 -ScrollCount 0`; it restores the original proxy in `finally`.
- For interactions: tap `评论`, then the top numeric interaction count such as `116`; look for fresh `FeedbackReactorsGraphService` responses.
- For shares: tap `评论`, then the separate top `X次分享` count such as `8次分享`; look for fresh `ResharesOfContentFeedQuery` responses. Never substitute the generic `分享按钮`.
- Do not record the former lock-screen password in project notes. A black screen during the earlier run was handled as emulator lock-screen/app-restart state, not as a data-reset instruction.

## UI Variants

- Not every post exposes the numeric share-count button. Modern feed cards often show only `分享按钮`; tapping it opens a share composer, not a reshare-user list.
- The Mothership post with 479 feed shares was captured in `runs/20260815-023801`, but its modern immersive detail UI (`mothership-detail-stable.xml`) exposed no numeric share-count node. Do not classify it as a share-list failure until a legacy/comment-detail entry is found.
- A successful share-user result requires both the UI count entry and a new `ResharesOfContentFeedQuery` response. UI text alone is insufficient.

## Data Boundary

- The returned actors are account-visible records, not a guarantee that the UI total is fully enumerable. In the successful sample, UI showed 8 shares while the response returned 3 actors and `has_next_page=false`.
- Never mix these three actors with the earlier 7-actor result when reporting a new batch.
