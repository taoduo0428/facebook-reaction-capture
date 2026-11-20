# Facebook Share / Reaction 任务当前状态

> 历史记录提示（2026-09-09）：本文件的“当前”指 2026-08-17 至 19 日阶段。最新状态见 `../CURRENT_STATUS_2026-09-09.md`。

更新时间：2026-08-17

## 1. 当前任务目标

当前项目解决两个实际任务：

1. 获取 Facebook 公开帖子当前账号可见的 Share / Reshare 用户；
2. 获取 Facebook 公开帖子当前账号可见的 Reaction / 点赞 / 表情用户。

目标不是强行让“可见用户数 = UI 总数”，而是在 Facebook 当前可见性规则内，把结果做成稳定、自动、可判断、可复现，并明确区分隐私差额与工程失败。

边界：

- 不重放 GraphQL；
- 不伪造 cursor；
- 不导出认证信息；
- 不自动互动；
- 不绕过 friends-only / visibility restriction。

## 2. 当前架构

```text
Facebook Android App
-> 真实 UI
-> App 自己发出 GraphQL
-> mitmproxy 被动观察脱敏响应
-> Python 解析 source_post_id / 用户 / page_info
-> batch summary
-> 传播网络数据
```

正式网络：

```text
正常：guest 127.0.0.1:7897 -> adb reverse -> host 127.0.0.1:7897
抓包：guest 127.0.0.1:8081 -> adb reverse -> mitmproxy -> upstream 127.0.0.1:7897
```

`10.0.2.2` 已废弃为旧方案。

## 3. Share 当前能力

已经实现：

- 真实 `X次分享` 数字入口；
- 评论/详情归一化；
- share-list 确认；
- Share query / user / page_info 提取；
- terminal / open；
- UI total / visible identity gap；
- fallback mismatch 早停。

最终分类：

- `enumerable`
- `visible-subset-terminal`
- `no-visible-share-users`
- `no-share-entry`
- `entry-or-query-failed`

代表样本：

| Run | UI Share | 可见用户 | 结果 |
|---|---:|---:|---|
| `20260816-090641` | 21 | 14 | terminal，gap 7 |
| `20260816-090843` | 20 | 3 | terminal，gap 17 |
| `20260816-091055` | 1 | 1 | terminal，gap 0 |
| `20260817-121052` | 124 | 33 | terminal |

`terminal=true` 只代表当前账号当前可见名单翻完，不代表平台全部身份公开。

## 4. Reaction 当前能力

默认先轻量筛选再 deep：

```text
Reaction list
-> 真实 reactor query
-> 有可见用户：enumerable -> deep
-> friends-only：visibility-restricted -> 快停
-> entry/query 工程失败：进入第二层 fallback 条件判断
```

已经实现：

- 多 tab；
- 多手势；
- growth tracking；
- 自适应预算；
- open / terminal group；
- fallback target guard；
- mismatch deep 不标 completed。

代表样本：

| Run | UI Reaction | 可见用户 | 说明 |
|---|---:|---:|---|
| `20260816-043511` | 1,341 | 426 | 26 pages |
| `20260816-044458` | 939 | 200 | 14 pages |
| `20260816-043404` | 364 | 0 | friends-only，deep=false |
| `20260817-113005` | 449 | 108 | 仍有 open groups |

## 5. 第二层 fallback 当前顺序

只针对工程失败，不针对明确隐私限制。

正式顺序：

```text
首轮 explorer
-> 唯一确定 source_post_id + author_id
-> exact target-open 严格验证
-> exact 无法证明时再尝试 bounded composite permalink
-> fallback Share / Reaction explorer
-> runtime family source guard
-> per-family 最终目标校验
-> target-filtered final result
```

Reaction 工程失败例：

- `query-not-triggered`
- `reaction-list-not-confirmed`
- `no-visible-reaction-count-entry`
- `no-reaction-count-after-comments-normalization`

Share 工程失败例：

- `no-share-entry`
- `entry-or-query-failed`

明确不 fallback：

- `friends-only-names`
- `query-triggered-friends-only-visibility`
- `visibility-restricted`

## 6. composite permalink 不能当作目标保证

2026-08-17 已 live 证明会串帖。

### Reaction 串帖

`runs\20260817-111700`

```text
expected = 1511174857690926
observed fallback Reaction source = 122272623644144568
```

### Share 串帖

`runs\20260817-113005`

```text
expected = 1557368649518003
observed fallback Share source = 1357431863226395
```

所以：

`fb://feed_story_permalink_non_encoded_id/<author_id>_<post_id>`

只能作为有界尝试，不能单独证明目标匹配。

## 7. runtime target guard 已 live 闭环

fallback explorer 接收：

- `ExpectedSourcePostId`
- `CaptureMinLine`

`facebook_capture_progress.py --min-line` 只统计 family 新记录，并给出：

`observed_source_post_ids`

如果 family source != expected：

- `target_guard=mismatch`；
- Share 不继续分页；
- Reaction deep 立即停止；
- mismatch 数据不进入最终目标结果。

`runs\20260817-113005` 的现场证据：

```text
SHARE_SCROLL_FILES = 0
PAGINATION_FILES = 0
```

即第一批错误 Share GraphQL 出现后立即停止。

## 8. 最终 per-family 校验

`native-permalink-fallback.json` 当前分别记录：

- `reaction_target_validation_status`
- `share_target_validation_status`

状态：

- `matched`
- `mismatch`
- `no-family-query`

只有对应 family 明确 `matched`，才允许：

- `reaction_permalink_fallback_recovered=true`
- `share_permalink_fallback_recovered=true`

`no-family-query` 不能借用 fallback 前旧数据制造 recovery。

mismatch fallback 的分页和用户不会并入目标 summary / network。

## 9. exact target-open 已完成严格化

`open_facebook_post_readonly.ps1` 当前不会把“搜索到目标数字”当作“已经打开目标帖”。

优先级：

1. 当前目标真实 target-specific URL；
2. 本地历史同 PostId 的真实 target-specific URL；
3. Facebook App 内精确 PostId 搜索；
4. 严格验证失败则返回失败，外层才考虑 composite。

不再硬拼：

`https://www.facebook.com/<author>/posts/<post>`

Generic `story.php` 不算 target-specific URL。

当前支持：

- pfbid
- story_fbid
- `/posts/<id>`
- `/permalink/<id>`
- `/reel/<id>`
- `/videos/<id>`

## 10. SearchResults 假阳性已修复

旧 run：

`runs\20260817-120342`

曾因为 SearchResultsGraphQL 中出现目标数字而错误标记 target-open success。

现在 Search family 全部只作为 discovery，不作为 verification。

目标数字可能只是：

`keywords_search(<PostId>)`

重新回归：

`SEARCH_FALSE_POSITIVE_BLOCKED=PASS`

即旧样本现在严格 analyzer exit 3，不再 false positive。

## 11. target-open live 兼容问题已逐项修复

2026-08-17 fresh 测试发现并修复：

### `runs\20260817-121052`

目标对象没有 `url` 属性。

修复：PowerShell 使用 `PSObject.Properties['url']` 安全读取；没有 URL 就继续 PostId 路径。

### `runs\20260817-121443`

search node 某些对象没有 `ResourceId`。

修复：target-open 节点 schema 统一提供：

`Label / ResourceId / Class / Clickable / X1 / Y1 / X2 / Y2 / Bounds`

### `runs\20260817-121851`

精确 PostId suggestion 合法按钮从 `Y=180` 开始，但旧条件是 `Y1 > 180`。

修复为：

`Y1 >= 180`

### `runs\20260817-122056`

按钮成功点击，但之后没有非搜索目标证据。

正确结果：

`verified=false / analyzer exit 3`

没有把搜索页面当目标帖。

## 12. 严格有界 target verification

目标打开后：

- verification window 从目标动作之后的 capture line 开始；
- Search family 永远不能 match；
- 只接受非搜索 GraphQL 中目标 PostId / target URL token；
- exit 0：matched；
- exit 3：窗口内每 2 秒重试；
- 其他 analyzer exit：立即失败；
- 验证上限：最低 8 秒、最高 20 秒，默认约 `WaitSeconds + 8`。

新增 diagnostics：

- `verification_attempts`
- `verification_elapsed_ms`
- `verification_timeout_seconds`

首个 capture 文件等待也统一到同一有界窗口，不会比严格验证窗口更早退出。

## 13. 最新 pfbid live 结论

`runs\20260817-122635`

目标：

`10163696286647957`

使用的是历史 Facebook 抓包中真实返回的 pfbid URL，而不是手工拼接。

完整约 14 秒验证窗口后：

```text
verified = false
reason = capture-jsonl-not-created
verification_elapsed_ms ~= 14150
verification_timeout_seconds = 14
```

说明当前 Facebook 555 的该 URL VIEW intent 没有触发可验证的目标帖子网络加载。

系统当前正确行为是 fail-safe，不制造 `matched`。

## 14. 当前测试与静态验收

最终 Python：

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

回归覆盖：

- fallback `min_line`；
- wrong-post source；
- SearchResults false positive block；
- URL token；
- matched / mismatch / no-family-query；
- mismatch pagination exclusion；
- friends-only never recovered；
- target-only summary / network。

## 15. 最终运行环境验收

```text
ADB=device
PROXY=127.0.0.1:7897
MARKER=False
REVERSE7897=True
REVERSE8081=True
LISTEN_8081=0
ACTIVE_CAPTURE_PROCESSES=0
```

没有残留抓包状态。

## 16. 当前完成度

### Share 主链路

已实现，已有多组 fresh / historical live 结果。

### Reaction 主链路

已实现，已有高覆盖与 friends-only live 结果。

### fallback 防错

已实现并 live 验证：

- 串帖识别；
- 第一批 family GraphQL 早停；
- 最终目标过滤；
- per-family recovered；
- SearchResults false positive block；
- exact target-open fail-safe。

### 当前仍缺的唯一现场正向样本

截至 2026-08-17，仍没有 fresh 第二层 fallback 样本能严格满足：

```text
fallback family source_post_id == expected target post_id
并且 fallback 实际恢复成功
```

正向 `matched` 分支已有单元测试覆盖，但不能声称已经 live 成功。

当前正确原则：

**必须严格证明 matched 才接受；不能证明就失败，不以错误帖子换取 recovery。**

本文件与 `2026-08-15_handoff-facebook-report.md`、`FACEBOOK_RESEARCH_GUIDE.md` 是当前 2026-08-17 基线。

## 17. 2026-08-19 中断恢复后的最新进度

本节覆盖 2026-08-19 继续执行后的真实代码与 live 结果；如与前文旧测试数量、旧限流判断冲突，以本节和 `predeploy-validation.json` 为准。

### 17.1 本轮已修复

1. **单 pagination run 的 PowerShell 数组解包异常**
   - live run `20260819-121028` 在正确识别 `session-restricted` 后，StrictMode 又因 `stop_reason` 属性访问异常退出；
   - 根因是仅有一个 pagination run 时，PowerShell `if` 表达式将数组解包为单个 OrderedDictionary；
   - 已强制 `$completionRuns = @(...)`；
   - `20260819-121411` 已 live 验证正常 exit 0，不再出现 `stop_reason` 异常。

2. **Reaction 底部 loader backpressure**
   - ADB run `20260819-121701` 与 native run `20260819-122008` 都能真实滚动 Reaction RecyclerView，但均未增长；
   - UI 树确认已经滑到底部并出现 `android.widget.ProgressBar`；
   - 已增加仅在明确看到底部 loader 时启用的有界短轮询，避免 loader 已在请求下一页时继续白滑；
   - `20260819-122854` 实际触发 `loader_backpressure_cycles=1`、`loader_backpressure_polls=13`，证明该分支已生效。

3. **source-less `1675004` 限流识别**
   - `20260819-122854` capture line 70 已确认真实发出 `FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection`；
   - Facebook 返回 HTTP 200 + GraphQL `1675004`，但该错误记录没有可识别 `source_post_id`，旧进度层因此误判成“无增长”；
   - `facebook_capture_progress.py` 现会在当前 Reaction family window 内独立汇总 rate-limit 状态，同时仍保持用户/页数据按目标 source 隔离；
   - 同一历史 capture 现正确返回：33 users、2 pages、`latest_rate_limited=true`、`latest_graphql_error_code=1675004`、`latest_rate_limit_line=70`；
   - PowerShell circuit breaker 已改用显式 `latest_rate_limit_line`，不再依赖目标 source 的 `latest_line`。

4. **gzip GraphQL request / cursor probe**
   - 最新 page2 请求体为 gzip；旧 addon 直接把 `raw_content` 当 UTF-8 解析，导致 form key 乱码、`request_cursor_present=false`；
   - 已增加有 `MAX_BODY_BYTES` 上限的 gzip/deflate 只读解压；
   - 使用真实 `mitmproxy.http.Request` 的离线验证已通过：`raw_gzip_magic=true`、`decoded_form=true`、`cursor_present=true`、`previous_end_available=true`、`cursor_matches_previous_end=true`、`raw_cursor_persisted=false`；
   - 仍然只保存 cursor 哈希/布尔连续性结果，不保存原始 cursor。

### 17.2 本轮 live 证据

- `20260819-121028`：命中 GraphQL `3252001` / API `368`，同时暴露单-run `stop_reason` 异常；
- `20260819-121411`：单-run 修复后正常 exit 0；
- `20260819-121701`：普通 ADB/burst，6 attempts，0 growth；
- `20260819-122008`：native controller 成功，`native_scroll_success=1`、2 native swipes、0 fallback，但仍 0 growth；
- `20260819-122854`：底部 loader backpressure 生效；随后离线解析确认 page2 response 为 GraphQL `1675004`；
- `20260819-123540`：当前 session 再次提前命中 `3252001 / 368`，程序在继续深分页前 fail-safe 停止，没有继续施压。

### 17.3 当前验证状态

- Python tests：**147/147 PASS**；
- PowerShell parser：**PARSER_OK**；
- `validate_facebook_predeploy.py`：**9/9 PASS**；
- overall：`PASS_OFFLINE_BLOCKED_LIVE`；
- 新增 predeploy 回归：
  - `historical-source-less-rate-limit-20260819-122854`：PASS；
  - `gzip-reaction-cursor-probe`：PASS。

### 17.4 当前真正阻塞项

当前不能把 3000+ 新版速度回归或 direct cursor page2+ live 标为完成，原因是 Facebook 当前 session 仍真实返回：

- Reaction connection pagination：GraphQL `1675004`；
- session temporary feature restriction：GraphQL `3252001` / API `368`。

这不是本地 swipe/native 是否工作的未知问题：当前已经证明列表可以正确滚动到 loader，page2 请求也确实发出；当前主要 live 阻塞在服务器/session 限制。

### 17.5 限制解除后的执行顺序

1. 先做小 cap page2+ probe，确认新 gzip request 解析在 fresh capture 中得到 `request_cursor_present=true` 且与上一页 end_cursor 哈希连续；
2. 若无 `1675004/368`，再跑 3000+ terminal speed regression；
3. 同一目标/同一干净 session 对比 ADB burst 与 native scroll 的 terminal 时间、growth/min、swipes/growth；
4. 再做最新 Share 全量回归；
5. 只有 ALL terminal 才允许声称 Reaction 完整采集。

### 17.6 2026-08-19 loader 节奏二次优化

根据 live UI 与 page2 抓包结果，进一步收紧“滑动过快、名单尚未加载完”的风险：

- `ReactionNoGrowthUiDumpEveryN` 默认值从 `3` 调整为 `1`，已同步 API、单次 capture、batch、explorer；
- 正常有增长时仍走 150ms live progress 快速路径，不增加 UI dump；
- 第一次无增长后立即检查 Reaction RecyclerView 底部是否出现 `ProgressBar`，不再等连续 3 次无增长才判断 loader；
- 看到 loader 后不再继续 swipe，loader backpressure 最长等待由约 4.4 秒提升为约 6.6 秒（当前默认参数下）；
- loader 等待期间会直接检查 source-less `1675004`，一旦出现立即交给现有 rate-limit circuit breaker，不必等 loader timeout 后才发现；
- `burst=2` 保留，因为现有 capture 只观察到一次实际 page2 connection request，没有证据表明 burst 本身会重复触发多个 page2 请求；
- 当前 session 仍有 `1675004` / `3252001` / `368` 历史与 fresh 证据，因此本轮不追加 3000+ live 压力测试。

本轮封板：Python tests `147/147 PASS`，PowerShell parser `PARSER_OK`，predeploy `9/9 PASS`，overall 仍为 `PASS_OFFLINE_BLOCKED_LIVE`。

### 17.7 2026-08-19 ReactionProbeOnly 健康探针

为避免每次判断 session 是否恢复都直接触发 page2 / 全量任务，新增真正的初始列表健康探针：

- 新参数：`-ReactionProbeOnly`，已接入 `run_facebook_capture.ps1` 与 `explore_facebook_readonly.ps1`；
- 新入口：`probe_facebook_reaction_health.ps1`；
- ProbeOnly 只允许严格目标验证、打开初始 Reaction 列表、source proof 与 screening；
- ProbeOnly 不点 reaction category tab，`ReactionScreenScrollCount` 强制为 `0`，不执行 page2 pagination，也跳过 fallback path；
- diagnostics 新增 `probe_only`、`probe_status`、`session_restricted`、`latest_rate_limited`、`latest_graphql_error_code`；
- `probe_status` 当前可能值包含 `initial-ready`、`blocked-session-restriction`、`blocked-rate-limit`、`not-ready`；
- wrapper 跑完会自动输出 `[probe-result]`，同时检查 `pagination_runs` 与 capture 中的 page2 request 数量；如果 ProbeOnly 意外产生分页，退出码会变为 `2`，避免静默破坏探针边界。

真实 live run `20260819-130426`：

- strict target source proof：matched，目标 `122106880826437311`；
- initial Reaction：31 users / 2 query records；
- `probe_status=blocked-session-restriction`；
- `session_restricted=true`；
- `deep_capture_requested=false`；
- `deep_pagination_runs=0`；
- capture 中 `FeedbackReactorsGraphService_At_Connection_Pagination...` 命中数为 `0`；
- 同一 run 仍捕获 GraphQL `3252001` / API `368`，因此当前不进行 3000+ 或 cursor page2+ live 压测；
- restriction 来源现可记录为 `session_restriction_friendly_name`；该 run 回放显示来源为 `FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery`；
- 历史成功全量 `20260818-192150` 与 `20260818-225729` 中均未出现 `3252001` 或 API `368`，因此当前仍保留 run-wide restriction gate，不因其来源不是 Reaction query 就放宽保护。

最新封板：Python tests `150/150 PASS`；`run / explorer / probe` 三个 PowerShell 脚本 `PARSER_OK_3`；predeploy `10/10 PASS`，其中 `reaction-probe-only-contract=PASS`；overall 仍为 `PASS_OFFLINE_BLOCKED_LIVE`。

下一次 live 顺序更新为：先运行 ProbeOnly；只有 `probe_status=initial-ready` 且 `page2_requests=0`、无 run-wide restriction 时，才允许进入小 cap page2+；小 cap page2+ 无 `1675004/368` 后，再进入 3000+ terminal speed regression。

### 17.8 2026-08-19 自动 Release Gate

为满足“无需人工盯进度、但不能在 Facebook 限制状态下持续施压”，新增完整机器门控：

- `facebook_release_gate.py`：对 Probe、page2、3000+ terminal、Share 结果做机器判定；
- `run_facebook_release_gate.ps1`：一键执行 `Probe -> conservative page2 -> ADB 3000+ -> Probe -> Native 3000+ -> Probe -> Share -> predeploy`；
- page2 验证阶段故意固定 `burst=1`、`native=0`、`absolute cap=2`、`rate-limit circuit=1`，先把正确性与速度变量分开；
- 只有 page2 fresh capture 同时证明 cursor present、previous end available、cursor hash continuity、target source match，且无 `1675004/3252001/368` 才进入 3000+；
- 重任务之间重新执行 ProbeOnly，避免前一轮刚触发限制后继续第二轮重任务；
- 3000+ 只有 `deep_capture_completed=true + terminal-complete + terminal + >=3000 users` 才通过；
- gate 状态实时写入 `facebook-release-gate-state.json`，blocked/failed/passed 都有明确 `current_stage / next_stage / stop_reason`；
- 新增 `test_facebook_release_gate.py`，使用真实历史 blocked probe、source-less `1675004`、3216 terminal run 做回归。

首次真实 gate `release-20260819-132802-probe` 暴露 PowerShell helper 输出污染：capture 子脚本的 stdout 被函数一起返回并拼进 `$runDir`。按 PowerShell pipeline 语义改为子脚本输出 `| Out-Host`，只保留真实 run path 作为函数返回值；随后测试覆盖该回归。

第二次真实 gate `release-20260819-133045-probe` 已正确工作：

- strict source proof：`matched`；
- initial Reaction：32 users / 2 query records；
- `pagination_runs=0`；
- `page2_requests=0`；
- `probe_status=blocked-session-restriction`；
- restriction source：`FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery`；
- gate 状态：`blocked-live`，`current_stage=probe`，没有进入 page2。

当前封板：Python tests `161/161 PASS`；四个关键 PowerShell 脚本 `PARSER_OK_4`；predeploy `11/11 PASS`（含 `release-gate-contract=PASS`）；overall 仍为 `PASS_OFFLINE_BLOCKED_LIVE`。

Release Gate 的 A/B 结果现在会自动记录 `adb_elapsed_ms`、`native_elapsed_ms`、`native_speedup_percent`、双方 `growths_per_minute`、`effective_swipes_per_growth`、`native_scroll_success/fallbacks`，并输出 `recommended_mode=adb|native`。只有 Native terminal run 真实出现 `native_scroll_success>0` 且两边都有有效 elapsed telemetry 时，A/B 才算可比较；否则 gate 在 `ab-comparison` fail-safe 停止，不会伪造性能结论。

Share gate 也已收紧：单纯 screening 阶段的 `enumerable` 不算全量回归通过；必须 terminal 或明确的最终可见性分类。历史 `20260817-121052`（124 UI shares -> 33 visible，3 pages，`visible-subset-terminal`，identity gap 91）已纳入 release gate 单测与 predeploy 合约。

已配置低频自动条件检查：每 6 小时运行一次 release gate。若 ProbeOnly 仍被 `1675004/3252001/368` 阻塞，不进入重任务且不通知；只有恢复为 `initial-ready` 才允许 gate 自动继续后续验收。此策略用于降低无效请求压力，不绕过 Facebook 限制。

### 17.9 2026-08-19 Release Gate 断点续跑与 A/B 选型加固

继续补齐无人值守场景，避免 gate 在后半程遇到限制后重复执行已完成的 3000+ 重任务：

- `ResumeMaxAgeHours=24`；只有同一 Post、24 小时内、旧 stage `passed=true`、run 目录仍存在的重阶段才进入复用候选；
- 每次新的 gate 仍强制 fresh `ProbeOnly -> conservative page2`，旧 Probe/page2 不复用，确保当前 session 健康是实时证明；
- `full-adb/full-native` 候选即使历史 PASS，也必须用当前 `facebook_release_gate.py inspect_full` 离线重新验证 `terminal-complete + >=3000`，不通过就重新执行，不盲信旧 state；
- 复用时写入 `reused_prior_stage=true` 与 `reused_from_gate_id`，便于最终报告区分 fresh / checkpoint；
- 这样如果 ADB 3000+ 已完成、随后 post-ADB Probe 被限制，下次恢复后不会重复 ADB 3000+，而是 fresh Probe/page2 后直接从未完成的 Native/Share 阶段继续。

A/B 推荐规则也从“谁稍快就选谁”收紧：

- Native 必须真实 `native_scroll_success>0` 才可比较；
- Native 要切成推荐默认，必须 `native_scroll_fallbacks=0` 且分页 elapsed 至少比 ADB 快 `5%`；
- 若 Native 只小幅领先 `<5%`，保留 ADB 稳定默认，reason=`native-gain-below-threshold-keep-adb`；
- 新增 `adb_users_per_minute / native_users_per_minute / recommendation_reason / native_fallback_free`，最终不仅比较 swipe，还比较实际用户吞吐量。

本轮最终离线封板：Python tests `163/163 PASS`；四个关键 PowerShell 脚本 `PARSER_OK_4`；predeploy `11/11 PASS`，overall `PASS_OFFLINE_BLOCKED_LIVE`。当前 live blocker 仍以 `release-20260819-133045-probe` 为准：source proof matched、32 users / 2 queries、page2=0，但 `blocked-session-restriction`，因此本轮没有追加任何 page2/3000+ live 压力。

外部实现/官方资料复核：Microsoft `about_Return` 与本次真实 bug 一致——PowerShell 函数会把 success pipeline 输出作为函数输出处理，因此 child capture stdout 必须与 `$runDir` 返回值隔离；Android Paging 官方 `LoadState` 文档将 append 明确区分为 Loading/NotLoading/Error，并提醒 load state 与 UI 数据应用存在同步时序，支持当前 loader backpressure 设计；mitmproxy HTTP API 明确说明 Content-Encoding 解码在异常编码下可抛 `ValueError`，mitmproxy GitHub #8124 也记录了 gzip header 与实际 body 不一致的真实案例，因此当前 request gzip/deflate 采用 bounded decode、失败即停止解析而非把二进制强行 UTF-8 化。以上复核没有引入任何绕过 rate limit / feature restriction 的行为。

### 17.10 2026-08-19 持久 checkpoint、独占锁与最终报告

继续审查无人值守长链路后，修复了两个会在多轮自动运行后暴露的问题，并补齐最终交付：

- 旧实现的断点依赖上一份 `facebook-release-gate-state.json`；若 ADB 3000+ 已 PASS、post-ADB Probe 被限制，随后下一轮 Probe 再次被限制，新 state 会覆盖旧 state，已完成的 ADB checkpoint 可能丢失；
- 新增独立 `facebook-release-gate-checkpoints.json`，只持久保存已 PASS 的 `full-adb / full-native / share`；fresh blocked Probe 只更新运行 state，不删除 checkpoint；
- checkpoint 仍受 `ResumeMaxAgeHours=24` 限制；复用不会刷新原始 live timestamp，避免旧 terminal 结果被无限续命；
- 如果 persistent checkpoint 与 prior state 同时有候选，选择时间最新、目录仍存在、仍在 24 小时窗口、`passed=true` 的候选，再用当前 inspector 离线复验；
- Share 同样支持 checkpoint，但每轮仍必须 fresh `pre-share-probe` 通过后才能复用；
- 新增 `facebook-release-gate.lock` 独占锁，使用 `FileShare.None`；第二个 gate 在任何 live capture 前直接 exit 60；异常退出残留文件在下一轮能独占打开时自动视为 stale 清理；
- 独占锁做了真实本地自测：人为持有 lock 后启动 child gate，child 明确 `ExitCode=60`，输出只有 `another release gate is already active; no live work started`，没有 transport/target-open/capture 日志；
- 新增 `facebook_release_report.py`；最终 PASS 自动生成 `facebook-release-final-report.json` 与 `facebook-release-final-report.md`，汇总 page2 cursor continuity、ADB/Native terminal 与性能、推荐模式、Share 可见性、checkpoint reuse 和 predeploy；report 生成失败会停在 `final-report`，不能把半成品标记 complete；
- 默认配置仍要求 Native A/B 与 Share；若未来显式关闭对应 stage，report 会按 gate 实际配置判断，不把主动 skip 误报失败。

最新离线封板：Python tests `171/171 PASS`；四个关键 PowerShell 脚本 `PARSER_OK_4`；predeploy `11/11 PASS`。`release-gate-contract` 中 persistent checkpoint、original age、Share checkpoint、final report、exclusive lock、lock release、A/B 规则均为 PASS。当前 live blocker 未重新施压，仍沿用 `release-20260819-133045-probe` 的 `blocked-session-restriction` 证据。

### 17.11 2026-08-19 子 PowerShell 确定路径与异常状态兜底

继续做无人值守环境审查时，发现嵌套宿主并不保证 `powershell` 名称能从 PATH 解析。为避免自动任务、IDE、桌面脚本、batch 使用不同宿主时偶发启动失败：

- `run_facebook_capture.ps1 / run_facebook_batch.ps1 / probe_facebook_reaction_health.ps1 / run_facebook_release_gate.ps1` 统一解析 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`，只有该路径不存在时才 fallback 到 `Get-Command powershell.exe`；
- 核心 capture 内 6 处子脚本调用、Probe wrapper、batch capture、release gate child capture 均改为 `& $PowerShellExe`；
- 项目级搜索后，当前执行脚本不再出现裸 `& powershell`，剩余匹配只存在于历史 backups/logs；
- 新增 predeploy `deterministic-powershell-entrypoints`，capture/batch/probe/release 四项必须全部 PASS，未来若重新引入 PATH 依赖会直接挡住预部署。

同一轮完整性检查发现 `facebook-release-gate-state.json` 残留一个 `20260819-140214` 的 `status=running`。核对时没有 `facebook-release-gate.lock`，对应 probe run 也不存在 `safe-facebook-http-capture.jsonl`，因此确认没有 Facebook live 请求，只是本地子进程启动前异常留下的假 running 状态。该 state 已纠正为 `failed`，stop reason=`stale-running-state-no-active-lock-or-capture`。

为防止以后再次出现类似假 running：

- release gate 新增脚本级 `trap`；未处理本地异常会把当前 state 改为 `failed`、写入 `unhandled-error: ...`、记录结束状态并释放独占锁，exit 70；
- trap 内使用 `Write-Warning` 而不是 `Write-Error`，避免在全局 `ErrorActionPreference=Stop` 下递归触发 trap；
- predeploy `release-gate-contract` 新增 `unhandled_errors_persist_and_release_lock=true`。

最新离线封板：Python tests `174/174 PASS`；五个关键 PowerShell 脚本（release/capture/batch/explorer/probe）`PARSER_OK_5`；predeploy `12/12 PASS`，overall 仍为 `PASS_OFFLINE_BLOCKED_LIVE`。本轮没有重新运行 Facebook Probe，live blocker 继续以 `release-20260819-133045-probe` 的 `blocked-session-restriction` 为准。

随后继续把同类宿主 PATH 风险扩展到 Python：当前机器实际 `py.exe=C:\Windows\py.exe`、`python.exe=...Python313\python.exe`；release/capture/batch/explorer/target-open/API wrapper 原先仍混用裸 `py/python`。现已统一优先 `%SystemRoot%\py.exe`，不存在时依次 fallback `Get-Command py.exe` / `python.exe`。核心分析器、progress helper、target verifier、batch summary、predeploy、final report、API starter 均改为 `& $PythonExe`。active `*.ps1` 全量搜索后，不再存在裸 `& python` / `& py` / 直接 `python ...`；剩余匹配仅在历史 backup。新增 predeploy `deterministic-python-entrypoints`，capture/batch/explore/target-open/api-start/release 六项全部 PASS；`run_facebook_batch.ps1` 与 `open_facebook_post_readonly.ps1` 同时加入 core-files。

最新总封板提升为：Python tests `176/176 PASS`；七个关键 PowerShell 入口（release/capture/batch/explorer/probe/target-open/API starter）`PARSER_OK_7`；predeploy `13/13 PASS`，overall `PASS_OFFLINE_BLOCKED_LIVE`。本轮仍没有增加任何 Facebook live 请求。

### 17.12 2026-08-19 批量队列、压力熔断与 Android ANR 恢复

为后续大量帖子任务补齐了真正的批量安全边界，而不是通过更激进重试规避 Facebook 限制：

- 新增 `facebook_batch_pressure.py`：离线读取单次 run，识别 `1675004 / 3252001 / API 368 / session-restricted / rate-limited`；命中任何平台限制时输出 `stop-batch + fresh-probe-before-resume`，禁止继续后续 live candidate；
- `run_facebook_batch.ps1` 接入自适应压力控制；terminal-clean 才允许逐步缩短候选间冷却，nonterminal 会增加冷却，blocked-live 立即熔断整批；
- 新增 `run_facebook_post_queue.ps1` 和 `facebook-post-queue.example.json`：每个帖子要求严格 `post_id + post_url`；未完成任务每次先 fresh Probe，只有 Probe PASS 才允许 full deep；只有 `terminal-complete` 才写帖子级 checkpoint；已 terminal 帖子下一轮直接跳过；
- 新增共享 `facebook-live-capture.lock`，由 `facebook_runtime.ps1` 提供获取/释放函数；release gate / batch / post queue 三者共享互斥，避免同时抢 emulator / ADB / 8081 / proxy；本地真实锁自测 `LIVE_LOCK_SELFTEST_OK`；
- 当前 `release-20260819-143833-probe` 离线送入 pressure controller，正确得到 `stop-batch`，block reasons=`session-restricted, graphql-3252001, api-368`；
- 首次 post queue smoke 暴露 Android 模拟器系统弹窗 `数字健康没有响应`（`com.google.android.apps.wellbeing` ANR），导致 target-open 没有生成 capture jsonl；Android Issue Tracker 也存在 Digital Wellbeing ANR 记录；
- target-open ANR 恢复已改为基于 Android `android:id/aerr_close` 的直接 UI 证据：遇到 ANR 对话框优先点“关闭应用”，失败再回退原 `Wait`；外层 strict retry 随后重新发 Facebook deeplink。普通 explorer 的 Wait/backoff 行为保持不变；
- 真实 smoke `runs/queue-facebook-post-queue.example-001-probe-20260819-155026`：strict attempt 1 检出并关闭 ANR；attempt 2 成功确认目标 `122106880826437311`，UI 显示约 `3304` reactions；Reaction Probe 得到 `34 users / 2 queries`，随后确认 `blocked-session-restriction`；queue 正确写成 `blocked-live` 并停止；项目搜索确认不存在对应 `queue-...-full-...` run，因此没有进入 page2/deep capture；
- queue state `facebook-post-queue-smoke.state.json` 已持久化当前 post、attempts、last probe run 与 `blocked-session-restriction`，后续恢复时不会把它误标 terminal。

最新封板：全套 Python tests `190/190 PASS`；target/queue/batch/release parser `PARSER_OK_4_TARGET_QUEUE`；predeploy `15/15 PASS`，overall 仍为 `PASS_OFFLINE_BLOCKED_LIVE`。本地批量架构已经可以在限制出现时自动熔断；当前唯一 live blocker 仍是 Facebook session restriction，而不是滚动、target-open、Android ANR 或 batch retry 逻辑。
