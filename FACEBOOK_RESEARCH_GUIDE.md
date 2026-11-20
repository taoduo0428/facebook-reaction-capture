# Facebook 公开帖子研究与抓包指南

> 当前状态入口（2026-09-09）：操作边界继续适用；最新 URL 实测、状态词和 M4 验收边界见 `CURRENT_STATUS_2026-09-09.md`。

更新时间：2026-08-17

## 1. 用途与边界

本目录用于授权的 Facebook Android 模拟器研究，目标是：

1. 获取公开帖子当前账号可见的 Reaction / 点赞 / 表情用户；
2. 获取公开帖子当前账号可见的 Share / Reshare 用户；
3. 把用户与帖子关系整理为 Reaction / Share 网络边。

正式方案只观察 Facebook App 自己发出的真实请求。

不会：

- 重放私有 GraphQL；
- 伪造 cursor；
- 导出 Cookie / Authorization / access_token；
- 自动点赞、评论、分享；
- 绕过 friends-only 或其他可见性限制。

## 2. 当前环境

| 项目 | 当前值 |
|---|---|
| 工作目录 | `C:\Users\lenovo\Desktop\facebook` |
| 模拟器 | `emulator-5554` |
| AVD | `fb-x86_64-api35` |
| Android | 15 / API 35 |
| Facebook | `com.facebook.katana` / `555.0.0.49.59` |
| 主机普通代理 | `127.0.0.1:7897` |
| guest 普通代理 | `127.0.0.1:7897`，通过 adb reverse |
| guest 抓包代理 | `127.0.0.1:8081`，通过 adb reverse |

旧的 `10.0.2.2:7897` / `10.0.2.2:8081` 不再作为正式网络方案。

## 3. 正式网络拓扑

正常：

```text
Facebook App
-> Android global proxy 127.0.0.1:7897
-> adb reverse tcp:7897 tcp:7897
-> Windows host 127.0.0.1:7897
-> Clash / mihomo
-> Internet
```

抓包：

```text
Facebook App
-> Android global proxy 127.0.0.1:8081
-> adb reverse tcp:8081 tcp:8081
-> host mitmproxy :8081
-> upstream 127.0.0.1:7897
```

抓包结束后必须恢复：

```text
proxy = 127.0.0.1:7897
reverse 7897 = present
reverse 8081 = present
capture marker = absent
8081 listener = absent
```

## 4. 推荐启动方式

优先使用：

`C:\Users\lenovo\Desktop\Facebook Stable Emulator.lnk`

或：

```powershell
.\start_facebook_stable.ps1
```

启动完成不是只看 proxy 字符串，而是同时确认：

- ADB=`device`；
- Android default network 存在；
- reverse 7897 / 8081 存在；
- guest 能实际连接 `127.0.0.1:7897`；
- Facebook UI 不显示无网络。

## 5. 日常采集命令

单候选 Reaction + Share：

```powershell
.\run_facebook_capture.ps1 `
  -Serial emulator-5554 `
  -LaunchFacebook `
  -OpenReactionList `
  -OpenShareList `
  -AutoScreenThenDeep `
  -ScrollCount 4 `
  -ReactionScrollCount 12 `
  -ReactionExtensionChunk 6 `
  -ReactionAbsoluteCap 60 `
  -ReactionTabLimit 5
```

批量：

```powershell
$offsets = @(3, 7, 11, 15, 19)
.\run_facebook_batch.ps1 `
  -FeedScrollOffsets $offsets `
  -ReactionScreenScrollCount 2 `
  -ReactionScrollCount 12 `
  -ReactionExtensionChunk 6 `
  -ReactionAbsoluteCap 60 `
  -ReactionTabLimit 5
```

不抓 Share：`-DisableShareList`

关闭第二层 fallback：`-DisableNativePermalinkFallback`

## 6. Share 判断

程序只点击真实分享次数入口，不把普通 Share composer 当作用户名单入口。

最终分类：

- `enumerable`：有可见分享用户；
- `visible-subset-terminal`：当前可见名单已 terminal，但 visible < UI total；
- `no-visible-share-users`：query 已 terminal，但当前无可见身份；
- `no-share-entry`：没有可靠分享数字入口；
- `entry-or-query-failed`：入口 / list / query 工程失败。

`has_next_page=false` 只表示当前账号可见列表翻完，不代表全平台完整名单。

代表样本：

- `runs\20260816-090641`：21 -> 14 visible，terminal，gap 7；
- `runs\20260816-090843`：20 -> 3，terminal，gap 17；
- `runs\20260816-091055`：1 -> 1，terminal，gap 0；
- `runs\20260817-121052`：UI Share 124 -> 33 visible，terminal。

## 7. Reaction 判断

默认 `AutoScreenThenDeep`：

```text
打开 Reaction list
-> 轻量探测
-> 有真实用户：enumerable -> deep
-> friends-only：visibility-restricted -> 快速停止
-> entry/query 工程失败：允许进入第二层 fallback 判断
```

深分页手势：

`long -> medium -> short -> rebound -> wait-long`

分页依据：

- query count；
- page count；
- user count；
- reaction group；
- `has_next_page`。

只要还有 open group，只能表述“当前已枚举 N 个可见用户”。

代表样本：

- `runs\20260816-043511`：UI 1,341 -> 426 visible / 26 pages；
- `runs\20260816-044458`：939 -> 200 / 14 pages；
- `runs\20260816-043404`：friends-only，0 visible，deep=false；
- `runs\20260817-113005`：UI 449 -> 108 visible，仍有 open groups。

## 8. 第二层 fallback：正式顺序

工程失败时，当前顺序是：

```text
首轮 explorer
-> 唯一确定 source_post_id + author_id
-> exact target-open 严格验证
-> exact 无法证明时，才尝试 bounded composite permalink
-> family explorer
-> runtime source guard
-> per-family 最终目标校验
-> target-filtered analyzer / batch summary
```

明确隐私限制不进入第二层 fallback：

- `friends-only-names`
- `query-triggered-friends-only-visibility`
- `visibility-restricted`

## 9. exact target-open 的规则

`open_facebook_post_readonly.ps1` 只接受可严格证明的目标打开。

目标打开优先级：

1. 当前目标记录里真实存在的 target-specific URL；
2. 本地历史中同一 PostId 的真实 target-specific URL；
3. Facebook App 内精确 PostId 搜索；
4. 无法严格验证时返回失败，由外层决定是否尝试 composite permalink。

不会再硬拼：

`https://www.facebook.com/<author_id>/posts/<post_id>`

因为 Facebook 555 可能接受该 URL，却导航到别的帖子。

Generic：

`https://m.facebook.com/story.php`

也不算 target-specific URL。

当前支持的 URL token：

- `pfbid...`
- `story_fbid`
- `/posts/<numeric-id>`
- `/permalink/<id>`
- `/reel/<id>`
- `/videos/<id>`

## 10. Search 只能用于发现，不能用于证明

`SearchResultsGraphQL` 和搜索 typeahead 数据不能证明帖子已经打开。

旧 run：

`runs\20260817-120342`

曾因搜索响应里出现目标数字而错误 `verified=true`。重新回归后现在必须得到 analyzer exit 3。

搜索响应中的目标数字可能只是：

`keywords_search(<PostId>)`

所以 target-open 只有看到**目标动作之后的非搜索 GraphQL**包含目标 PostId / URL token，才允许 `verified=true`。

## 11. target-open 有界验证

目标打开后采用严格有界轮询：

- verification window 从目标动作之后的 capture line 开始；
- Search family 永远不能匹配；
- analyzer exit 0：严格 matched，立即成功；
- exit 3：尚未匹配，在窗口内每 2 秒重试；
- 其他 analyzer error：立即失败；
- 验证上限：最低 8 秒、最高 20 秒，默认约 `WaitSeconds + 8`。

Diagnostics：

- `verification_min_line`
- `verification_attempts`
- `verification_elapsed_ms`
- `verification_timeout_seconds`
- `verified`
- `reason`
- `analyzer_exit`

最新 pfbid live：

`runs\20260817-122635`

使用 Facebook 历史抓包中真实返回的 pfbid URL，完整约 14 秒后仍没有创建目标抓包记录：

```text
verified = false
reason = capture-jsonl-not-created
verification_elapsed_ms ~= 14150
```

因此当前 Facebook 555 的该 URL VIEW route 不能视为可靠目标打开路径。

## 12. composite permalink 只能作为最后尝试

Facebook 555 runtime 中存在：

```text
fb://feed_story_permalink_non_encoded_id/{id}
fb://feed_story_permalink_non_encoded_id/{id}?open_reactors_list=true
```

当前 composite：

`<author_id>_<post_id>`

但它会串帖。

Reaction 串帖：

- `runs\20260817-111700`
- expected `1511174857690926`
- observed fallback Reaction source `122272623644144568`

Share 串帖：

- `runs\20260817-113005`
- expected `1557368649518003`
- observed fallback Share source `1357431863226395`

因此 composite 启动成功不等于 matched。

## 13. runtime source guard

fallback explorer 接收：

- `ExpectedSourcePostId`
- `CaptureMinLine`

`facebook_capture_progress.py --min-line` 只分析 family 新记录，并输出：

`observed_source_post_ids`

一旦 family source 与目标不一致：

- `target_guard=mismatch`；
- Share 不再分页；
- Reaction deep 立即停止；
- mismatch deep 不标 completed。

`runs\20260817-113005` 已 live 验证：

```text
SHARE_SCROLL_FILES = 0
PAGINATION_FILES = 0
```

## 14. 最终 per-family 校验

`native-permalink-fallback.json` 包含：

- `reaction_target_validation_status`
- `share_target_validation_status`

状态：

- `matched`
- `mismatch`
- `no-family-query`

只有对应 family 明确 `matched`，才可能：

- `reaction_permalink_fallback_recovered=true`
- `share_permalink_fallback_recovered=true`

`no-family-query` 不会借用 fallback 前旧数据制造 recovery。

错帖分页不会合并进目标 summary / 传播网络。

## 15. 结果文件

单 run：

```text
runs\<timestamp>\safe-facebook-http-capture.jsonl
runs\<timestamp>\analysis\facebook-sample-patterns.json
runs\<timestamp>\analysis\facebook-posts.csv
runs\<timestamp>\analysis\facebook-share-users.csv
runs\<timestamp>\analysis\facebook-reactor-users.csv
runs\<timestamp>\readonly-ui\exploration-diagnostics.json
runs\<timestamp>\fallback-target-open\target-open-diagnostics.json
runs\<timestamp>\native-permalink-fallback.json
runs\<timestamp>\readonly-ui-fallback\exploration-diagnostics.json
```

Batch：

```text
batches\<batch-id>\batch-summary.json
batches\<batch-id>\batch-summary.csv
```

传播网络：

```text
facebook-network.json
facebook-network-edges.csv
facebook-network.dot
```

## 16. 当前验收基线

Python：

```text
Ran 35 tests
OK
```

PowerShell parser：

```text
run_facebook_capture.ps1         OK
open_facebook_post_readonly.ps1  OK
explore_facebook_readonly.ps1    OK
run_facebook_batch.ps1           OK
facebook_transport.ps1           OK
start_facebook_stable.ps1        OK
```

最终运行环境：

```text
ADB=device
PROXY=127.0.0.1:7897
MARKER=False
REVERSE7897=True
REVERSE8081=True
LISTEN_8081=0
ACTIVE_CAPTURE_PROCESSES=0
```

## 17. 当前真实完成度

已经 live 证明：

- Share / Reaction 主链路可运行；
- 正常成功帖不会误触发 fallback；
- 工程失败会进入 fallback；
- composite 会串帖；
- 串帖会第一批 family GraphQL 早停；
- 错帖不会污染最终目标结果；
- SearchResults 假阳性已被堵住；
- URL / PostId target-open 无法证实时会 fail-safe；
- 网络和 capture 状态会正常恢复。

截至当前仍没有 fresh 第二层 fallback 样本能严格证明：

`family source_post_id == expected target post_id`

并同时恢复成功。

因此当前原则是：**严格 matched 才接受；无法证明就失败，不以错误帖子换取“成功率”。**

当前主交接：`2026-08-15_handoff-facebook-report.md`
