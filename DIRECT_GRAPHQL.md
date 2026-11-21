# 直发 GraphQL 模式

跳过 UI 滚动，直接对 `graph.facebook.com/graphql` 发分页请求，拿完 reaction / share 名单。

实测（帖子 `1691321539667513`，949 reactions + 45 shares）：

| 模式 | 耗时 | reaction | share |
|---|---|---|---|
| UI 滚动 | 242 秒 | 949 | 16 |
| 直发（首次） | 134 秒 | 948 | 16 |
| 直发（省重复分析） | **121 秒** | 948 | 16 |

---

## 一、怎么用

### 主流程（推荐）

```powershell
.\run_facebook_capture.ps1 -Serial emulator-5554 `
  -PostUrl 'https://www.facebook.com/100063900504939/posts/1691321539667513' `
  -LaunchFacebook -OpenReactionList -OpenShareList `
  -DirectGraphql `
  -OutputDir runs\my-run
```

不加 `-DirectGraphql` 就是原来的 UI 路径，行为完全不变。

可选参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `-DirectGraphqlSleepMs` | 1000 | 每页间隔（毫秒），**不要低于 500** |
| `-ExtraPostIds` | 空 | 额外帖子 ID（逗号分隔），复用同一 token 直发 |
| `-AutoExtraPosts` | 关 | 从 target-open 抓到的 feed 里自动发现帖子，全部批量直发 |
| `-AutoExtraPostLimit` | 10 | 自动发现的上限（防止一次抓太多触发限流） |

### 自动发现整条时间线

不想一个个指定 ID，就让程序从 feed 抓包里自己找：

```powershell
.\run_facebook_capture.ps1 -Serial emulator-5554 `
  -PostUrl 'https://www.facebook.com/100063900504939/posts/1691321539667513' `
  -LaunchFacebook -OpenReactionList -OpenShareList `
  -DirectGraphql -AutoExtraPosts -AutoExtraPostLimit 5 `
  -OutputDir runs\auto-run
```

target-open 打开帖子时，feed 里已经带回了十几条帖子的 ID，自动提取后一起直发。

实测（主帖 + 自动发现 5 条）：

```
[direct] auto-discovered 5 extra posts from the target-open feed
[direct] reactions finished in ...
```

自动发现的能力边界：

- 只能发现 **target-open 那一刻 feed 里已经加载的帖子**（约 10–20 条），不会无限翻 feed
- 复合 ID（`<owner>_<post>`）取尾段作为 post id
- 返回 0 行的帖子会被自然跳过（无权限 / 没 reaction / 已删除）

### 多帖子批量

主帖子走完整 target-open（验证 + 拿 token），额外帖子复用同一 token 直发：

```powershell
.\run_facebook_capture.ps1 -Serial emulator-5554 `
  -PostUrl 'https://www.facebook.com/100063900504939/posts/1691321539667513' `
  -LaunchFacebook -OpenReactionList -OpenShareList `
  -DirectGraphql `
  -ExtraPostIds '1643052717863108,1595294725960334' `
  -OutputDir runs\batch-run
```

额外帖子的有效性由返回行数判断：0 行表示帖子不存在、无权限或没有 reaction，会从输出里自然缺席（不会报错中断）。

### 单独跑（不启动 App、不开抓包）

前提：已有一份含 OAuth token 的抓包。

```powershell
.\run_direct_capture.ps1 `
  -RawMitm 'runs\live-verify-full\raw-facebook-http.mitm' `
  -PostId '1691321539667513' `
  -TemplateJson 'worker-bootstrap\graphql-templates.json' `
  -IncludeShares `
  -OutputDir runs\direct-only
```

多帖子批量：

```powershell
.\run_direct_capture.ps1 -RawMitm <抓包> -PostIds '111,222,333' `
  -TemplateJson worker-bootstrap\graphql-templates.json -Workers 2 -OutputDir runs\batch
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `-PostId` / `-PostIds` | 必填其一 | 单个或逗号分隔多个 |
| `-SleepMs` | 1000 | 每页间隔 |
| `-Workers` | 2 | 并发帖子数 |
| `-MaxPages` | 200 | 单个帖子最大页数 |
| `-IncludeShares` | 关 | 是否抓 share |
| `-Append` | 关 | 追加写 jsonl（不覆盖已有记录） |
| `-SkipAnalyze` | 关 | 跳过内置分析（主流程统一分析时用） |
| `-NoResume` | 关 | 忽略已保存的游标，从第 1 页重来 |
| `-TemplateJson` | 空 | 模板文件路径 |
| `-Workers` | 2 | 并发帖子数，**不要超过 2** |

### 断点续传

每翻完一页就把游标写进 `<OutputDir>/state/<post_id>.json`。中途被限流打断后，**下次运行同一个 `-OutputDir` 会自动从断点继续**：

```
resuming FeedbackReactorsGraphService at page 27
```

不用从头重抓，也不会重复拉已经拿到的 26 页。

想强制从头来就加 `-NoResume`（会先清空 `state/` 目录）：

```powershell
.\run_direct_capture.ps1 -RawMitm <capture> -PostId 1691321539667513 `
  -OutputDir runs\my-run -NoResume
```

状态文件长这样，可以手工检查进度：

```json
{"friendly": "FeedbackReactorsGraphService", "cursor": "AQHR...", "pages": 26,
 "saved_at": "2026-10-31T11:30:00Z"}
```

帖子翻完（`has_next_page=false`）时对应的状态文件会被自动删除，所以 `state/` 里剩下的就是**没抓完的帖子**。

---

## 二、模板机制（关键）

移动端 GraphQL 用 `client_doc_id`（持久化查询 ID）而不是查询文本。**doc_id 绑定 APK 版本**，不同版本的 App 不能混用。

### 两级来源

```
token      ←  每次从当前抓包取（--mitm）
doc_id + variables  ←  优先 --template-json，其次 --mitm
```

为什么必须分开：`target-open` 阶段只打开帖子详情页，抓包里**没有** reaction/share 的 query，学不到 doc_id。所以模板要从一次完整抓包预生成。

### 生成模板

```bash
python facebook_graphql_direct.py --mitm <完整抓包> --dump-templates worker-bootstrap/graphql-templates.json
```

当前模板文件含 58 个，其中关键 4 个：

```
FeedbackReactorsGraphService
FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection
ResharesOfContentFeedQuery
ResharesOfContentFeedQuery_At_Connection_Pagination_Feedback_reshares_connection
```

### 什么时候要重新生成

- Facebook App 升级后（doc_id 可能变）
- 报 `friendly_name not in capture` 或 `query-not-triggered`
- 换账号后仍报模板缺失

判断方法：

```bash
python facebook_graphql_direct.py --mitm <新抓包> --list
```

列表里没有目标 query 就说明该次抓包不含它，不是 doc_id 变了。

---

## 三、分页机制

两段式：

```
第 1 页   FeedbackReactorsGraphService
          variables: { feedback_id, reactors_connection_first: 25, ... }

第 2+ 页  FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection
          variables: { ..., reactors_connection_after_cursor: <cursor>,
                          paginationPK: "feedback:<post_id>", orderby: ... }
```

`paginationPK` 是 `base64("feedback:<post_id>")`，**不是偏移量**。

服务端硬限制：`reactors_connection_first` 设成 100 / 500 / 1000 / 5000 都只返回 **25 行**。没有流式接口，没有批量接口，只能翻页。949 个 reaction = 38 页。

---

## 四、安全限制（不要调低）

| 项 | 值 | 理由 |
|---|---|---|
| 每页间隔 | ≥ 1.0 秒 | 公开参考阈值 300 次 / 600 秒（0.5 req/s） |
| 并发帖子 | ≤ 2 | 单账号并发过高先触发 1675004 |
| 最大页数 | 200 | 防止异常帖子无限翻 |

测试里断言了 `sleep >= 0.9` 和 `workers 默认 2`，改小会挂测试。

### 限流信号

| 信号 | 含义 |
|---|---|
| GraphQL code `1675004` | reaction 操作被限流 |
| GraphQL code `3252001` | 临时封禁 |
| GraphQL code `368` | api 错误（通常是频率） |
| HTTP 429 | 请求过多 |
| 文案 `temporarily blocked` | 临时封禁 |
| 文案 `use this feature at the moment` | 功能被限 |
| 文案 `suspended your account` | 账号被停 |
| 文案 `login approval needed` | 需要登录审批 |

代码里 `RATE_LIMIT_CODES = {1675004, 3252001, 368}`，命中后指数退避（上限 10 秒）。**限流且响应没有 `page_info` 时直接抛 `RateLimitedError`** —— 否则会把它当成正常最后一页，静默截断名单。封禁文案抛 `BlockedError`。

### 限流了怎么办

Facebook 限流的响应是 **HTTP 200 + 错误体**：

```json
{"errors": [{"message": "Rate limit exceeded", "severity": "CRITICAL", "code": 1675004}],
 "extensions": {"is_final": true}}
```

用单请求探测判断恢复情况（只发 1 个请求，不翻页、不重试、不写抓包）：

```bash
python check_rate_limit.py --mitm runs/live-auto/raw-facebook-http.mitm \
  --template-json worker-bootstrap/graphql-templates.json
```

| 输出 | 退出码 | 含义 |
|---|---|---|
| `CLEAR: 2 page(s) answered, cursor=yes` | 0 | 已恢复，可以继续 |
| `THROTTLED: graphql codes [1675004] on page 2` | 3 | 仍在限流，继续等 |
| `BLOCKED: ...` | 4 | 账号级封禁，不是限流 |
| `probe failed: ...` | 2 | 抓包/模板/网络问题，结论无效 |

**必须用 `--pages 2` 或更多**。Facebook 可以正常返回第 1 页、只对带游标的第 2 页限流；只看第 1 页会误判成“已恢复”。探测脚本会传分页模板，所以第 2 页是真正的游标请求。

实测过的一个反例：`--pages 1` 报 CLEAR，紧接着直发在第 2 页就拿到 `1675004`。原因是探测当时没传 `next_friendly`，所谓“第 2 页”其实是又请求了一次第 1 页。这个坑已经修掉。

恢复节奏参考：

| 触发强度 | 建议等待 |
|---|---|
| 单次 `1675004` | 30–60 分钟 |
| 连续多轮触发 | 2–6 小时 |
| 出现 `3252001` 或封禁文案 | 12–24 小时，且换账号 |

---

## 五、输出

```
<OutputDir>/
├── safe-facebook-http-capture.jsonl   每页一条，与 mitmproxy addon 同格式
├── direct-reactions.csv               post_id,user_id,user_name,reaction_id
├── direct-shares.csv                  （-IncludeShares 时）
├── direct-summary.json                统计
└── analysis/                          分析器产物
    ├── facebook-reactor-users.csv
    ├── facebook-share-users.csv
    ├── facebook-posts.csv
    ├── facebook-sample-patterns.json
    └── ...
```

直发写的 jsonl 注入了 `request_feedback_post_id`，所以现有 `analyze_facebook_capture.py` 能直接消费，不需要改分析逻辑。

---

## 六、已知限制

1. **比 UI 少 1 个 reaction**：直发从 `reactors_connection_first=25` 开始翻，不含作者自己的 `viewer_feedback_reaction_info`。稳定差 1，不是随机丢数据。
2. **share 拿不全**：帖子显示 45 次分享，实际只能拿到 16 个用户。这是 FB 隐私限制，UI 路径同样如此，分析器会标 `limited: true` / `identity_gap: 29`。
3. **doc_id 绑定 APK 版本**：实测 27 天内未变，但 App 升级后必须重新生成模板。
4. **需要一份含 token 的抓包**：不能凭空发请求。token 有效期约 1 小时。

---

## 七、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `friendly_name not in capture` | 抓包里没有该 query | 用 `--template-json`，或重新生成模板 |
| `capture has no ResharesOfContentFeedQuery; shares skipped` | Reels 不产生 share query | 正常，Reels 没有分享列表 |
| `query-not-triggered` | 记录里没有 feedback 绑定 | 检查 `request_feedback_post_id` 是否注入 |
| `ConnectTimeout` | 本机连不上 graph.facebook.com | 设 `FACEBOOK_UPSTREAM_PROXY` 或 `--proxy` |
| 只跑 1 页就停 | `has_next_page` 为 false | 检查 `feedback_id` 是否正确 |
| `RateLimitedError` | 触发限流 | 调大 `-SleepMs`，等 10 分钟 |

---

## 八、相关文件

| 文件 | 作用 |
|---|---|
| `facebook_graphql_direct.py` | 直发客户端（分页、限流检测、jsonl 输出） |
| `run_direct_capture.ps1` | 直发运行器（单/多帖子） |
| `worker-bootstrap/graphql-templates.json` | 持久化模板（58 个） |
| `tests/test_facebook_graphql_direct.py` | 28 个测试 |
