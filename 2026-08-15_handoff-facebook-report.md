# Facebook 帖子互动用户抓取项目交接

> 历史记录提示（2026-09-09）：本文保留 2026-08-19 当时事实。当前 URL 类型、完整分页、热启动和 macOS 边界请先看 `CURRENT_STATUS_2026-09-09.md`；本文中的“当前”不再代表今天状态。

更新时间：2026-08-19

> 本文件已同步 2026-08-18～2026-08-19 当前对话与上一轮 Facebook 对话的关键内容、负责人要求、真实 run 结果、性能优化、API、cursor 探索、Share/Reaction 状态、部署方向与安全边界。后续如旧章节数字与本文件“最新状态”冲突，以 2026-08-19 最新状态为准。

---

## 0. 接手先看：当前结论

项目当前已经不再处于“能不能抓到 Facebook Reaction 用户”的早期阶段。

Reaction 方向已经完成以下真实验证：

```text
200+：已验证
1000+：已验证
3000+：已验证
terminal：已验证
用户 ID / 用户名 / 头像覆盖：已验证
API -> Android App -> 抓包 -> analyzer -> API result：已验证
```

当前核心任务已经从：

```text
“能不能抓全当前账号可见的 Reaction 用户”
```

转为：

```text
“在不破坏 source_post_id / terminal / 可见性语义的前提下，
把 3000+ 用户的完整采集从十几～三十分钟压到几分钟。”
```

最新性能路线：

```text
live progress
-> wait-long 状态机修正
-> burst pipeline
-> Android persistent/native scroll
-> cursor transport 可行性验证
```

其中 cursor 的协议链已经明确成立，但完全脱离 UI 的主动 cursor 深分页还没有正式生产验收通过，历史小实验 page2 遇到 Facebook `1675004 Rate limit exceeded`。

---

## 1. 项目目标与负责人要求

原始任务是通过 Facebook 手机端 App，针对公开帖子获取两类当前账号可见身份：

1. Reaction / 互动用户：Like、点赞及其他表情反应用户；
2. Share / Reshare 用户：当前账号可以枚举到的转发用户。

这些数据用于构建帖子传播网络、点赞网络、转发网络，理解话题传播链条与关键节点。

负责人/用户反复确认的核心要求：

- 整个流程要尽量自动化，不需要人一直点击/下滑；
- 一个 1000+ 点赞帖子不能在 200 左右因为程序自身限制停止；
- Reaction 用户结果至少尽量包含：
  - `user_id`
  - `user_name`
  - `avatar_url`
  - `source_post_id`
  - 可解析时的 `reaction_id / source_field`
- Share 也必须区分平台可见性限制与程序失败；
- “全量”只能建立在真实 `terminal=true` 上；
- `absolute-cap`、超时、人为停止、UI 仍 open 都不能写成全量；
- 严格防串帖，目标帖无法证明时宁可失败，也不能把别的帖子的用户混入结果；
- 用户希望长任务过程中能看到真实中间进度，不要长时间无输出。

### 当前统一语义

正式口径：

> Facebook 对当前登录账号可见、可枚举多少，就稳定采集多少；平台本身不展示/不允许枚举的身份，标记为可见性限制，而不是程序失败。

`terminal=true` 表示：

> 当前登录账号在当前 Facebook App / 当前入口下可枚举的列表已经到达终点。

它不等价于：

> Facebook 全平台理论上的所有用户全部被获取。

---

## 2. 当前正式环境

工作目录：

```text
C:\Users\lenovo\Desktop\facebook
```

Android / Facebook：

```text
Emulator: emulator-5554
AVD: fb-x86_64-api35
Android: 15 / API 35
Facebook package: com.facebook.katana
Facebook version: 555.0.0.49.59
```

主机正常代理：

```text
127.0.0.1:7897
```

正式网络：

```text
Facebook App
-> guest 127.0.0.1:7897
-> adb reverse tcp:7897 tcp:7897
-> host 127.0.0.1:7897
-> Clash / mihomo
-> Internet
```

抓包网络：

```text
Facebook App
-> guest 127.0.0.1:8081
-> adb reverse tcp:8081 tcp:8081
-> host mitmproxy/mitmdump :8081
-> upstream 127.0.0.1:7897
```

`10.0.2.2:7897 / 8081` 是旧方案，不再作为正式基线。

推荐启动：

```text
Facebook Stable Emulator.lnk
start_facebook_stable.ps1
```

---

## 3. 抓包原理：不是“数学破解 TLS”

正式路线是受控 Android 测试环境中的 MITM 抓包：

```text
Facebook Android App 真实 UI
-> App 自己发出的 GraphQL
-> mitmproxy 被动观察响应
-> 本地 analyzer / batch summary / network data
```

HTTPS 能被看到，是因为测试模拟器环境已经具备证书信任 / SSL pinning 处理，使 App 流量能够经过受控 mitmproxy 会话。

不是：

```text
截获 Facebook TLS 密文
-> 破解密钥
-> 解密
```

而是：

```text
让测试 App 流量主动经过受控代理
-> mitmproxy 看到 App 自己实际发送/接收的 HTTP/GraphQL
```

项目历史材料包括：

```text
SSL-Pinning-Bypass-ADB.pdf
SSL-Pinning-Bypass-ADB.txt
patched-libs\
burp.der
```

当前正式主抓包器是 mitmproxy / mitmdump，不是 Burp Suite。

---

## 4. 核心文件

```text
start_facebook_stable.ps1
facebook_transport.ps1
facebook_network_watch.ps1
run_facebook_capture.ps1
run_facebook_batch.ps1
explore_facebook_readonly.ps1
open_facebook_post_readonly.ps1
facebook_capture_addon_safe.py
facebook_capture_progress.py
analyze_facebook_capture.py
facebook_batch_summary.py
build_network_data.py
facebook_capture_api.py
start_facebook_capture_api.ps1
```

主要测试：

```text
test_analyze_facebook_capture.py
test_facebook_batch_summary.py
test_facebook_capture_api.py
test_facebook_capture_orchestrator.py
test_facebook_live_progress.py
```

相关文档：

```text
2026-08-15_handoff-facebook-report.md
FACEBOOK_RESEARCH_GUIDE.md
docs/2026-08-16_facebook-share-reaction-task-status.md
```

---

## 5. Golden Light 主验证帖

主性能/全量验证目标：

```text
URL:
https://www.facebook.com/61563119350848/posts/122106880826437311

post_id:
122106880826437311

author/page:
61563119350848
```

这个帖子持续增长，因此不同时间的 UI total 会变化。比较 run 时不要把不同时间点的 Reaction 总数差异误判成解析错误。

---

## 6. 严格 source_post_id 防串帖机制

这是项目目前最重要的正确性保护之一，不允许为了速度删除。

当前要求：

1. 先严格打开目标帖；
2. 验证 resolved post ID；
3. exact PostId 匹配后才进入 Reaction/Share 深分页；
4. explorer 接收 `ExpectedSourcePostId` 和 `CaptureMinLine`；
5. Reaction summary 入口必须来自严格确认的 post-detail RecyclerView，不能泛扫 feed；
6. 深分页前必须拿到 fresh family `source_post_id` 证据；
7. 新 family source 与目标不一致时：
   - 不接受增长；
   - 不继续翻页；
   - 不把错误用户写入目标结果；
8. analyzer 再做一层 source 过滤，避免历史 capture 边界污染。

### 已经真实出现过的串帖

Reaction：

```text
runs\20260817-111700
expected = 1511174857690926
observed fallback Reaction source = 122272623644144568
```

Share：

```text
runs\20260817-113005
expected = 1557368649518003
observed fallback Share source = 1357431863226395
```

因此：

```text
fb://feed_story_permalink_non_encoded_id/<author_id>_<post_id>
```

只能作为有界 fallback，绝不能单独作为目标证明。

### runtime guard live 证据

`runs\20260817-113005`：错误 Share source 第一批出现后即早停：

```text
SHARE_SCROLL_FILES = 0
PAGINATION_FILES = 0
```

说明 guard 不是只存在于单元测试，而是 live 闭环过。

---

## 7. exact target-open 与 fallback 当前设计

目标打开优先级：

1. 当前目标真实记录中已有、带 post-specific token 的 URL；
2. 本地历史相同 PostId 的真实 target-specific URL；
3. Facebook App 内精确 PostId 搜索；
4. exact 无法严格验证时，外层才尝试 bounded composite permalink。

禁止：

- 不再硬拼 `https://www.facebook.com/<author>/posts/<post>` 作为证明；
- generic `story.php` 不算 target-specific URL；
- SearchResultsGraphQL 不能证明帖子已经打开。

支持的目标 URL token 包括：

```text
pfbid...
story_fbid
/posts/<id>
/permalink/<id>
/reel/<id>
/videos/<id>
```

### SearchResults 假阳性修复

旧 run：

```text
runs\20260817-120342
```

曾因 SearchResultsGraphQL 中出现目标数字而误标 `verified=true`。

现在 Search family 只能 discovery，不能 verification。

回归：

```text
SEARCH_FALSE_POSITIVE_BLOCKED=PASS
```

### target-open UI 兼容修复

2026-08-17 fresh 测试修过：

- 目标对象没有 `url` 属性时不再 strict-property 崩溃；
- search node 统一 `ResourceId / Bounds / Class / Label` schema；
- suggestion 合法 bounds 从 `Y=180` 开始时，条件改为 `Y1 >= 180`。

### pfbid live 结论

`runs\20260817-122635`

目标：

```text
10163696286647957
```

使用真实抓包里历史返回的 pfbid URL，完整约 14 秒验证窗口后：

```text
verified = false
reason = capture-jsonl-not-created
verification_elapsed_ms ~= 14150
```

说明当时 Facebook 555 的该 URL VIEW intent 没有触发可验证的目标帖子网络加载。系统正确 fail-safe，没有制造 matched。

---

## 8. Reaction summary 临时缺失 retry

当目标帖已经严格验证，但 Reaction count 临时没有出现，且原因精确为：

```text
no-reaction-count-after-strict-target-detail-scan
```

允许重新打开同一个原始 Post URL/PostId，最多 2 次。

每次 retry 必须满足：

```text
exit = 0
verified = true
resolved_post_id == PrimaryVerifiedPostId
fresh capture boundary
same ExpectedSourcePostId
```

不能为了提高成功率扩大 feed 扫描范围。

---

## 9. Reaction terminal / partial 的正式语义

Facebook App 的 Reaction 列表是 cursor 分页/懒加载：

```text
打开 Reaction 列表
-> App 请求第一页
-> RecyclerView 向下移动
-> App 用上一页 cursor 请求下一页
-> mitmproxy 捕获响应
-> analyzer 累计用户
-> has_next_page=false
```

正式 terminal 依据：

- GraphQL connection 明确 `has_next_page=false`；
- source_post_id 仍是目标帖；
- family 没有跨 source 污染；
- analyzer 识别到终止页。

以下不能写成全量：

```text
absolute-cap
timeout
manual stop
process interruption
UI trigger exhaustion but has_next_page=true
partial-open-tail
```

历史上 1350 用户的 partial-open-tail 曾被误解为全量，已修正：

```text
absolute-cap + has_next_page=true
=> partial-open-tail
=> 不是 terminal
=> 不是全量
```

---

## 10. Reaction 历史关键验证

### 75 用户

`runs\20260818-153717`

```text
exact target
source proof exact
75 unique visible
4 query records
terminal=false
```

测试 cap 较低停止。

### 200 用户

`runs\20260818-154558`

```text
200 unique users
9 query records
stop = absolute-cap
```

结论：旧的约 200 停止是程序 cap，不是 Facebook 平台硬上限。

### 1350 用户

`runs\20260818-155010`

```text
~1350 unique visible users
__ALL__ ~54 pages
UI total 当时约 3135
attempts = 180
stop = absolute-cap
terminal=false
open=true
401/403/429 = 0
```

证明 >1000 可枚举。

### 12-attempt 速度基线

`runs\20260818-170416`

```text
12 attempts
125 unique
6 query records
growth = 4
terminal=false
open=true
ui_dump_count = 0
deep ~39s
~3.3s / attempt
401/403/429 = 0
ANR = false
```

### 40-attempt 稳定性基线

`runs\20260818-170728`

```text
40 attempts
375 unique
16 query records
growth = 14
terminal=false
ui_dump_count = 2
deep ~119s
~2.98s / attempt
401/403/429 = 0
ANR = false
```

### 40 次 live-progress 验证

`runs\20260818-191028`

```text
attempts = 40
growth = 15
400 unique users
source exact
live cycles = 40
fallback cycles = 0
```

### 180 次深分页

`runs\20260818-191338`

```text
attempts = 180
growth = 65
1650 unique users
stop = absolute-cap
source exact
```

再次证明不存在 200/1000 固定硬上限。

### terminal run A

`runs\20260818-192150`

```text
attempts = 366
growth = 126
stop = terminal
terminal = true
3180 unique users
UI total 当时约 3182
live = 366
fallback = 0
401/403/429 = 0
总时长约 12.9 分钟
```

### terminal run B

`runs\20260818-195425`

```text
attempts = 363
growth = 127
stop = terminal
terminal = true
3187 unique users
UI total 当时约 3189
source_post_id = 122106880826437311
live = 363
fallback = 0
ui_dump_count = 23
401/403/429 = 0
总时长约 12.4 分钟
```

历史记录中还出现过：

```text
3180 / 3182 terminal
3216 / 3217 terminal
```

UI total 与可枚举身份可能差 1～数个，不能自动归因于程序漏抓，可能来自可见性、特殊账号、隐私或计数时间差。

---

## 11. API 全链路

本地 API：

```text
facebook_capture_api.py
127.0.0.1:8765
```

主要 endpoint：

```text
POST /v1/jobs
GET  /v1/jobs/{id}
GET  /v1/jobs/{id}/result
POST /v1/jobs/{id}/cancel
GET  /health
```

2026-08-19 已补齐任务取消：排队任务可直接取消；运行中的任务会终止当前 capture 子进程树；`cancelled` 状态会写入 job manifest 并在 API 重启后保留。对已经 `completed / failed / cancelled` 的任务重复调用 cancel 会返回明确的 no-op 结果，不会破坏已经完成的 result。

异步 Job 架构是正确方向：一个 Android worker 运行时，其他任务排队，不允许多个 API 请求同时抢同一个模拟器。

### Production 默认语义

```text
-PostUrl
-ScrollCount 0
-AutoScreenThenDeep
-DurationSeconds 0
-ReactionAbsoluteCap 0
-MaxFlows 0
```

0 表示不使用固定上限强制结束：

```text
DurationSeconds=0        -> 不设固定总时长终止
ReactionAbsoluteCap=0    -> 不设固定 pagination attempt cap
MaxFlows=0               -> addon 不因 flow 数量停止
```

### 3000+ API terminal 全链路验收

Job：

```text
fb_51f4205ff9234678
```

结果：

```text
status = completed
error = null
Reaction users = 3209
unique IDs = 3209
user_name coverage = 3209/3209
avatar_url coverage = 3209/3209
wrong source_post_id = 0
terminal = true
rate_limit_http_seen = false
auth_http_error_seen = false
HTTP = 200 / 204 / 206 only
```

证明完整链路：

```text
POST /v1/jobs
-> worker
-> Facebook App
-> Reaction deep pagination
-> terminal
-> analyzer
-> GET result
```

真实通过。

该轮从约 22:08 到 22:40，总时长约 31 分 40 秒。功能正确，但速度明显不合理，成为后续性能优化重点。

API 结果字段禁止使用容易误导的：

```text
all_users
all_reactors
all_sharers
```

应该明确是当前登录账号可见/可枚举结果，并保留：

```text
target_validation
terminal
limited
ui_total
visible_count
```

---

## 12. 性能优化：live progress

`facebook_capture_addon_safe.py` 新增：

```text
facebook-live-progress.json
```

目的：

- addon 收到 Reaction/Share 响应后实时更新轻量状态；
- explorer 不再每次重新扫完整 JSONL；
- snapshot 不保存 token/cookie/auth/request body；
- final analyzer 仍以 safe JSONL 为最终事实来源。

Reaction live state 包含：

```text
source_post_id
reaction group
unique count
query/page count
latest line
open
terminal
```

`explore_facebook_readonly.ps1` 采用 live-first：

```text
Get-LiveCaptureProgress
-> 安全时直接使用
-> capture boundary 混合或数据不足时 fallback 到 facebook_capture_progress.py
```

严格 source guard 不变。

验证过的快速参数：

```text
ReactionLiveProgressPollIntervalMs = 150
ReactionLiveProgressPollTimeoutMs  = 1200
fallback interval = 500ms
fallback timeout  = 2200ms
ReactionMinCycleMs = 700
```

---

## 13. 31 分钟慢的主因：wait-long 粘住

API 3209 terminal 那轮性能剖析发现：

- 最开始几次 `long` 没增长后进入 `wait-long`；
- 后续大量分页长期粘在 `wait-long`；
- `wait-long` 每次存在额外等待；
- 因此把本应十几分钟级的完整任务拉到了 30+ 分钟。

状态机已修改为：

> `medium / short / rebound / wait-long` 仅作为解卡探针；只要任一恢复策略重新产生新页，下一次立即回到最快的 `long`。

不能再让 `wait-long` 连续跑几百页。

---

## 14. burst pipeline：不再“一滑一等”

用户提出的正确方向：

> 抓包是异步的，只要 UI 持续产生下一页请求，就不应该“一次 swipe -> 等一次包 -> 再 swipe”。

因此分页核心改为流水线：

```text
连续 long swipe
-> 批末读一次 live progress
-> 已经有新包：立即继续下一 burst
-> 包没到：才做短轮询/backoff
```

近期默认：

```text
BurstSize = 2
SwipeGap = 150ms
LongSwipe = 300ms
LivePoll = 150ms
```

进一步做了：

> burst 结束后先 0ms 读取 live progress。如果包已经到了，连 150ms 都不等。

新增压力诊断：

```text
burst_count
burst_swipes
burst_growth_hits
no_growth_bursts
effective_swipes_per_growth
strategy_resets_to_long
```

这套参数已经贯穿手工 capture / batch / API 默认构造逻辑，接手时仍需再次读取最新源码确认当前实际默认值，不能只凭聊天记录。

---

## 15. Android persistent/native scroll

继续优化后发现，剩余大头不只是抓包等待，而是热循环里反复执行：

```text
adb shell input swipe
```

造成进程启动开销，同时 Facebook RecyclerView 自己还有渲染/加载成本。

因此下一层已经开始接入 Android 端常驻 native scroll controller：

```text
Android persistent controller
-> 直接 scroll/fling RecyclerView
-> mitmproxy 后台异步收包
-> burst boundary 做 source/terminal 检查
```

真实 run：

```text
runs\20260819-001433
```

被人为停止做检查前：

```text
350 unique users
15 pages
open=true
terminal=false
509 source guard checks
0 mismatch
0 ANR
```

该 run 不是失败，也不是 terminal，只是手工中止。

接手时要核实最新源码中的：

```text
ReactionNativeScrollEnabled
BurstSize
SwipeGap
LongSwipe
```

尤其不要根据旧记录假设 native 已经默认开启。

---

## 16. Cursor 分页：最新协议证据

为什么 cursor 值得研究：

UI 下滑只是为了促使 Facebook App 发下一页请求。如果能在当前已登录会话中安全地继续 cursor 分页，就可以去掉大部分 RecyclerView 渲染和 scroll 成本。

基本链：

```text
page1
-> end_cursor=A
-> next request after=A
-> page2
-> end_cursor=B
-> ...
-> has_next_page=false
```

### `20260818-195425` terminal 抓包统计

文件：

```text
runs\20260818-195425\safe-facebook-http-capture.jsonl
```

统计：

```text
Reaction records/pages = 129
users = 3187

has_next_page=true  = 128
has_next_page=false = 1
end_cursor present  = 129/129

__ALL__ pages = 128
specific reaction group = 1 page
```

`__ALL__` 用户分布：

```text
前 127 页 × 25 用户
最后 1 页 × 12 用户
= 3187
```

结论：

- Reaction cursor 深分页协议成立；
- 每一页都有 `page_info`；
- 每一页都有 `end_cursor`；
- 最后一页明确 `has_next_page=false`；
- 不存在 200/1000 的协议硬上限；
- UI 下滑本质只是触发下一页 cursor 请求。

### 主动 cursor 小实验

上一轮 Facebook 对话里已做过小规模主动验证：

- 首页真实 `FeedbackReactorsGraphService` 返回 25 人；
- 首页有 `end_cursor`；
- `has_next_page=true`；
- page2 使用的 cursor 与 page1 返回的 `end_cursor` 匹配；
- target 仍为 `122106880826437311`。

但是 page2 当时返回：

```text
Rate limit exceeded
error code = 1675004
```

15 秒冷却后仍未稳定拿到 page2 数据。

当时已考虑/实现过的保护思路包括：

```text
ALL-first
checkpoint
指数退避
熔断
先检查 rate-limit 再判断 growth
非敏感请求结构指纹
```

正式结论：

```text
Cursor 协议可行性：已验证
3187 terminal 链：已验证
约 25 人/页：已验证
无 200/1000 硬上限：已验证

完全脱离 UI 的主动 cursor 深分页：尚未正式验收通过
历史 page2：遇 1675004
```

不能把“cursor 匹配成功”写成“无 UI cursor 已经跑通”。

### safe capture 为什么不能直接重放

`safe-facebook-http-capture.jsonl` 故意主要保存：

```text
friendly_name
host
method
path
request_body_bytes
request_body_sha256
status_code
response_content_type
response_body_bytes
response_body
...
```

并不保存完整敏感认证材料。因此 safe capture 足够证明协议、分页规模和 terminal，但不能仅靠它还原完整已认证下一页请求。

后续 cursor 探索必须遵守：

- 当前账号可见数据边界；
- 不绕过认证；
- 不破解/伪造签名；
- 不提取、传播 token/cookie；
- 不做平台风控规避；
- 遇限流停止或合理退避，而不是硬冲。

---

## 17. Share 当前能力与语义

Share 路径已识别：

```text
ResharesOfContentFeedQuery
```

已实现/历史验证过：

- 真实 `X次分享` 数字入口；
- comments/detail 归一化；
- share-list 确认；
- Share query / user / page_info；
- terminal/open；
- UI total / visible identity gap；
- wrong source fallback 早停。

分类：

```text
enumerable
visible-subset-terminal
no-visible-share-users
no-share-entry
entry-or-query-failed
```

代表样本：

```text
runs\20260816-090641  UI 21  -> 14 visible, terminal, gap 7
runs\20260816-090843  UI 20  -> 3 visible, terminal, gap 17
runs\20260816-091055  UI 1   -> 1 visible, terminal, gap 0
runs\20260817-121052  UI 124 -> 33 visible, terminal
```

`has_next_page=false` 只表示当前账号可见 Share 名单已经翻完，不代表平台全部分享者公开。

当前近期工作的 live 重心集中在 Reaction 3000+，所以接手后需要用最新仓库 run 再核 Share 最终状态，不要从旧聊天直接声称 Share 已经“全量”。

---

## 18. 主页隐私 vs 公开 Reaction / Share 实测

2026-08-18 专门验证：用户主页对非好友受限，是否意味着其公开帖互动身份也一定隐藏。

Reaction 明确实证用户：

```text
Ingrid Kypena
user_id = 100060409312511
```

当前账号与其不是好友，个人主页显示“加为好友可查看更多帖子”。但她仍真实存在于公开帖 GraphQL `reactors` 名单：

```text
source_post_id = 1520742620068282
user_id = 100060409312511
user_name = Ingrid Kypena
reaction_id = 1635855486666999
source_field = reactors
```

结论：

> 主页帖子对非好友受限，不等于该用户在公开帖子上的 Reaction 身份必然隐藏。只要 Facebook 当前会话在公开 Reaction 列表返回该用户，就可以采集其当前可见 ID/用户名。

Share 方向当时检查了大量用户，包括 100+ Share 名单用户和约 61 个新 Share 用户，其中约 41 个明确显示“加为好友”。但尚未找到与 Ingrid 一样“主页明确受限 + 仍在公开 Share 名单”的完全同构样本。

因此 Share 这条特殊组合不能伪装成已经 live 证明。

---

## 19. MCP 网络权限修复

Web MCP Assistant 重新安装后，本地持久设置曾变回：

```text
permissionMode = safe
```

导致 Facebook 网络测试出现：

```text
NETWORK_PERMISSION_REQUIRED
```

定位：

```text
C:\Users\lenovo\AppData\Roaming\web-mcp-assistant\settings.json
```

只修改：

```text
permissionMode = trusted
```

备份：

```text
settings.json.before-trusted-20260818-190233
```

随后 MCP 子进程参数确认：

```text
--permission-mode trusted
```

并通过：

```text
127.0.0.1:7897 -> Facebook -> HTTP 200
```

验证网络恢复。

注意：ChatGPT connector endpoint/token 与 Local Coding Tools MCP 自身 permission mode 是两层，连接器能连不等于本地服务已经允许联网。

---

## 20. 压测与性能验收标准

每次性能测试至少记录：

```text
run id
target post id
UI reaction total
unique users
query/page count
attempt count
growth count
terminal/open
stop reason
source_post_id mismatch count
401
403
429
1675004/platform rate-limit
ANR
proxy restore state
capture process residual state
total duration
deep pagination duration
```

性能专项还应记录：

```text
live_hits
fallback_hits
ui_dump_count
live_poll_cycles
fallback_poll_cycles
burst_count
burst_swipes
burst_growth_hits
no_growth_bursts
strategy_attempts
strategy_growth
strategy_resets_to_long
effective_swipes_per_growth
```

不能只比较“总分钟数”，因为 target-open retry、页面初始状态和 Facebook 自身响应会造成波动。

正确测试顺序：

```text
12-trigger smoke
-> 40-trigger stability/A-B
-> 确认明显加速且无回归
-> 3000+ terminal
-> API 3000+ terminal
```

不要每改一点就浪费 30 分钟跑完整全量。

---

## 21. 当前不应回退的设计

不要回退到：

```text
10.0.2.2 proxy
固定 200 users cap
固定 180 attempts 作为生产全量上限
每次 swipe 后固定长 sleep
每次 no-growth 都完整 UI dump
泛扫 feed 找 reaction 数字
source mismatch 仍继续
absolute-cap 写成 terminal
partial-open-tail 写成全量
API 默认固定 DurationSeconds
API 默认固定 ReactionAbsoluteCap
```

也不要为了速度：

```text
关闭 source guard
隐藏 401/403/429
忽略 ANR
把 UI total 强行当作一定可枚举人数
```

---

## 22. 测试基线

较早阶段曾有：

```text
35 tests PASS
PowerShell parser PASS
```

随着 API/live-progress/performance 测试加入，历史上又达到：

```text
124 tests + 9 PowerShell parser PASS
129 Python tests + 9 PowerShell parser PASS
```

测试数量会随文件变化。后续接手不要只复述旧数字，必须实际跑当前仓库。

核心 Python 回归至少包括：

```text
python -m unittest test_analyze_facebook_capture.py test_facebook_batch_summary.py test_facebook_capture_api.py test_facebook_capture_orchestrator.py test_facebook_live_progress.py
```

PowerShell 核心脚本也要 parser 校验。

---

## 23. Facebook Capture Service 交付方向

上一轮 Facebook 对话已经把最终形态从“研发脚本”定义成可部署服务：

```text
给 API 一个 Facebook Post
-> 后台检查 Android worker
-> 自动打开 Facebook App
-> 自动打开目标帖子
-> 自动 Reaction/Share
-> mitmdump 抓 App 自己返回的 GraphQL
-> analyzer 严格 target filter
-> API 返回结构化结果
```

异步接口建议：

```text
POST /v1/jobs
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/result
POST /v1/jobs/{job_id}/cancel
GET  /health
```

`POST /v1/jobs/{job_id}/cancel` 已于 2026-08-19 补齐，并覆盖 queued、running、重复 cancel、持久化恢复与 HTTP route 单元测试。

不要在当前研发目录直接 `git init -> add all`。正确路线：

```text
C:\Users\lenovo\Desktop\facebook  研发现场保留
        -> 抽取成熟能力
        -> facebook-capture-service
        -> GitHub Private Repository
```

GitHub 只保存代码、测试、安装脚本、配置样例和文档，不保存：

```text
Facebook 密码
Cookie/token/auth header
登录后的 AVD 用户数据
真实抓包 JSONL
真实 Reaction/Share 用户 CSV
runs/
batches/
mitm 私钥
真实 .env secrets
个人截图
```

---

## 24. MacBook M4 / 24 小时住宅节点计划

目标部署机器是 Apple Silicon MacBook M4：

```text
Windows 当前：x86_64 host -> x86_64 AVD
Mac M4：arm64 host -> arm64-v8a AVD
```

不能直接复制当前 x86_64 AVD 到 M4。

目标 Mac 结构：

```text
MacBook M4
├─ Facebook Capture API
├─ Job Queue
├─ Android Emulator ARM64
│  └─ Facebook App
├─ ADB Controller
├─ mitmdump
├─ Analyzer
└─ Health / Watchdog
```

部署环境定位：家里 24 小时接电、家庭宽带、专用 Facebook/后续 Instagram Android worker。

Mac 正式版不能强依赖 Windows 的 Clash 路线，需支持：

```text
NETWORK_MODE=home_direct
Android -> mitmdump -> 家庭宽带 -> Facebook
```

同时可选：

```text
NETWORK_MODE=upstream_proxy
```

代码与运行数据必须分离：

```text
~/facebook-capture-service/                    Git 代码
~/Library/Application Support/FacebookCaptureService/  jobs/captures/logs/runtime/secrets
```

24 小时运行还需要：

- 自动启动；
- Emulator / ADB / mitmdump watchdog；
- Facebook 登录失效检测；
- Job timeout/cancel；
- stale job 处理；
- 日志轮转；
- capture 保留期限；
- 磁盘告警；
- `/health`；
- 优雅关闭；
- 重启后半截任务不能误报成功。

远程 API 不建议裸露家庭公网端口。推荐：

```text
Tailscale / WireGuard / SSH Tunnel / 企业私网
-> 家里 MacBook
-> localhost API
```

---

## 25. Instagram 后续扩展

Facebook 稳定后再扩展 Instagram，不同时重构两套。

目标结构：

```text
API / Job Manager
├─ Facebook Worker
│  └─ Facebook AVD/App
└─ Instagram Worker
   └─ Instagram AVD/App
```

先复用 Job、network、health、logging、platform 层，再新增 Instagram adapter。

---

## 26. 当前明确未完成 / 不要误报完成

1. 完全脱离 UI 的 cursor 主动深分页尚未正式通过；历史 page2 遇 `1675004`。
2. native scroll 已进入实现/测试阶段，但接手时必须核实当前默认是否真正启用。
3. 3000+ 在最新 burst/native 组合下还需要新的正式 terminal 性能成绩，不能拿旧 12 分钟或 31 分钟冒充新优化结果。
4. Share 最近没有像 Reaction 一样做新的 3000+ 级专项验证，最终状态应查最新 run。
5. API cancel 已完成；长期服务化运维（日志轮转、磁盘策略、长期 watchdog、Mac 启动托管等）仍需继续完善。
6. MacBook M4 只是正式目标，不是已经 live 验收完成的平台。
7. exact target-open positive fallback 当时仍缺一个 fresh、严格 `family source_post_id == expected` 且真正恢复成功的 live 正向样本；系统当前原则仍是 fail-safe。

---

## 27. 当前下一步优先级

### P0：核对最新源码真实默认

重点检查：

```text
ReactionNativeScrollEnabled
BurstSize
SwipeGap
LongSwipe
ReactionLiveProgressPollIntervalMs
ReactionLiveProgressPollTimeoutMs
ReactionMinCycleMs
wait-long reset-to-long
API default command builder
```

### P1：短压测

先做：

```text
12-trigger
40-trigger
```

要求：

```text
source exact
long 为主
wait-long 不粘
burst growth 正常
ANR=0
401/403/429=0
```

### P2：native vs ADB A/B

同一帖子比较：

```text
ADB input swipe
vs
persistent/native scroll
```

看：

```text
seconds/page
seconds/25 new users
swipes/growth
ANR
source mismatch
```

### P3：3000+ terminal 新速度验收

```text
ReactionAbsoluteCap=0
DurationSeconds=0
MaxFlows=0
```

要求：

```text
terminal=true
source exact
users 接近当前 UI 可枚举量
无 auth/rate-limit fatal
API result 完整
```

### P4：cursor transport

先做很小规模低频验证：

```text
page1 -> page2 -> page3
```

只有在不绕过认证/风控、并且能稳定通过 `1675004` 限流边界的情况下，才值得继续独立 paginator。

### P5：Share 最终验收

重新核当前最新 Share run，确认：

```text
source exact
terminal / limited 语义
visible identity gap
API result
```

---

## 28. 安全与事实边界

交接/代码/日志中不要写入或传播：

```text
真实 cookie
token
authorization header
密码
敏感请求正文
私密账号信息
mitm 私钥
```

不要为了所谓“全量”去做：

```text
认证绕过
签名破解
风控规避
批量账号切换
```

出现 Facebook 平台限流时，正确策略是识别、退避、停止或回退稳定 UI 方案，而不是规避平台机制硬冲。

---

## 29. 给后续 AI 的回答口径

### “能不能自动化，不需要人一直点？”

目标是可以。生产设计：

```text
API submit
-> 自动 target-open
-> PostId verification
-> 自动 Reaction/Share
-> 自动 pagination
-> terminal
-> analyzer
-> API result
```

### “1000 个赞会不会只抓 200？”

已经真实证明不会：

```text
1350 partial
1650 partial
3180 terminal
3187 terminal
3209 API terminal
3216 terminal（历史记录）
```

200 是旧测试 cap，不是平台固定上限。

### “返回的是点赞用户信息吗？”

是，当前 Reaction 结果目标至少包括：

```text
user_id
user_name
avatar_url
source_post_id
reaction_id/source_field（有数据时）
```

API 3209 terminal run 中：

```text
ID 3209/3209
name 3209/3209
avatar 3209/3209
wrong source = 0
```

### “terminal 是不是 Facebook 全部人？”

不是。

它只表示：

> 当前登录账号在当前 App 入口下可见/可枚举列表已经耗尽。

---

## 30. 最重要的接手原则

1. 先查真实 run，不要只看聊天里一句“好了”。
2. `terminal` 与 `partial` 必须严格分开。
3. `source_post_id` 防串帖优先级高于速度。
4. 先 12/40 次短压测，再跑 3000+。
5. 新优化必须和旧基线 A/B，不要只报“感觉更快”。
6. 长任务持续输出真实中间进度。
7. 已证明功能后，把时间花在真正瓶颈上，不要反复做无价值的 3000+ 全量验证。
8. 任何“全部”都必须加“当前登录账号可见/可枚举”的限定。
9. cursor 协议成立不等于主动 cursor 生产方案已跑通。
10. 不以错误帖子、认证绕过或风控规避换成功率。

---

## 31. 当前一句话状态

截至 2026-08-19：

> Reaction 已完成 1000+、3000+、terminal、ID/用户名/头像覆盖和 API 全链路真实性验证；历史 3216 人 / 130 页 terminal API 全量约 12m55s。当前性能代码已加入 150ms live progress、burst pipeline、native scroll、首次无增长 loader 检测与 loader backpressure；page2 请求已实抓到，但当前 session 会返回 `1675004`，并存在 run-wide `3252001 / API 368`。gzip GraphQL request 已能安全解压并验证 cursor hash continuity，原始 cursor 不落盘。当前 `ReactionProbeOnly + run_facebook_release_gate.ps1` 已形成完整无人值守门控：fresh Probe -> conservative page2 -> ADB 3000+ -> Probe -> Native 3000+ -> A/B -> Probe -> Share -> predeploy -> final report；任何限制立即 fail-safe 停止。真实 gate `release-20260819-133045-probe` 已证明 source matched、32 users / 2 queries、0 page2，并准确停在 `blocked-session-restriction`。断点续跑已从“上一份 state”升级为独立 `facebook-release-gate-checkpoints.json`：已 PASS 的 `full-adb/full-native/share` 不会被后续 blocked Probe state 覆盖；每轮仍 fresh Probe/page2，checkpoint 只在 24 小时内、同一 Post、目录存在、最新候选、当前 inspector 再验证通过时复用，并保留原始 live timestamp，不允许无限续命。新增 `facebook-release-gate.lock` 独占锁，真实自测 child gate 在锁占用时 ExitCode=60 且没有任何 live capture 日志，避免自动任务与人工运行同时抢 ADB/mitmproxy。A/B 规则要求 Native 真实工作、无 fallback 且至少比 ADB 快 5% 才推荐切 Native，否则保留 ADB；记录 users/min、growths/min、swipes/growth 与 recommendation reason。最终 PASS 自动生成 `facebook-release-final-report.json/.md`，report 失败不会标记 complete。当前 `190/190` Python tests PASS、target/queue/batch/release parser `PARSER_OK_4_TARGET_QUEUE`、predeploy `15/15 PASS`；capture/batch/probe/release 子 PowerShell 已统一使用 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`（fallback `Get-Command powershell.exe`），release/capture/batch/explorer/target-open/API wrapper 的 Python 子进程也统一优先 `%SystemRoot%\py.exe`（fallback `Get-Command py.exe/python.exe`），不再依赖宿主 PATH；release gate 另有脚本级 trap，未处理本地异常会写 `failed/unhandled-error` 并释放 lock，避免残留假 running。自动条件检查每 2 小时低压运行，限制未解除时不跑重任务，恢复后自动继续 gate。Share 历史 `20260817-121052` 的 124 UI shares -> 33 visible / 3 pages / `visible-subset-terminal` 已纳入回归，但仍需在干净 session 下完成最新 fresh Share 回归。

本文件现在作为 Facebook 项目 2026-08-19 最新主交接基线；更细的 2026-08-19 修复证据见 `docs/2026-08-16_facebook-share-reaction-task-status.md` 第 17 节。

---

## 32. 后续大量帖子运行入口

后续如果任务从单帖扩大到大量明确帖子，优先使用 `run_facebook_post_queue.ps1`，输入格式见 `facebook-post-queue.example.json`。每个任务必须提供严格 `post_id + post_url`。

队列的安全语义：

```text
已有 terminal checkpoint -> 跳过
未完成 -> fresh Probe
Probe blocked -> 整队停止 live work
Probe ready -> full Reaction deep
full terminal -> 保存帖子级 checkpoint
平台限制 1675004 / 3252001 / 368 -> 熔断整队，下一轮先 fresh Probe
```

`facebook_batch_pressure.py` 负责压力判定；`facebook-live-capture.lock` 保证 release gate、旧 batch、新 post queue 不会同时抢 emulator / ADB / 8081 / proxy。不要为了提高吞吐量删除这两个保护层。

2026-08-19 15:50 左右真实 queue smoke 已验证：Android 模拟器曾被“数字健康没有响应”ANR 覆盖。target-open 现会根据 `android:id/aerr_close` 直接识别 ANR 对话框并优先关闭阻塞应用；随后 strict retry 成功重新打开目标帖。真实 run `queue-facebook-post-queue.example-001-probe-20260819-155026` 成功确认目标帖，Reaction Probe 得到 34 users / 2 queries，然后因 `blocked-session-restriction` 正确停止；没有创建 full queue run。因此当前大量任务的阻塞点仍是 Facebook session restriction，不是队列、target-open 或 Android ANR。
