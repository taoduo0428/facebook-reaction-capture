# Facebook Share / Reaction native fallback 研究与当前实现

> 历史记录提示（2026-09-09）：本文件记录 8 月研究过程。当前入口覆盖、首屏 Probe、完整分页和 macOS 边界以 `../CURRENT_STATUS_2026-09-09.md` 为准。

更新时间：2026-08-17

## 1. 研究目标

本文件只记录 Share / Reaction 在没有明确隐私限制、但首轮 UI / query 工程失败时的第二层补救。

边界不变：

- 不导出 Facebook Cookie / token；
- 不重放 GraphQL；
- 不伪造 cursor；
- 不手工构造私有 Parcelable / `ProfileListParams`；
- 不绕过 friends-only；
- fallback 仍让 Facebook App 自己导航并发出真实 GraphQL。

## 2. 已淘汰的旧 Web 路径

历史社区方案包括：

```text
m.facebook.com/ufi/reaction/profile/browser/?ft_ent_identifier=<post_id>
m.facebook.com/browse/shares?id=<post_id>
```

当前 Facebook 555 环境实测：

- Chrome 没有 App 登录态，只到登录页；
- 项目不导出 App Cookie/token 给 Chrome；
- `/browse/shares` 交给 Facebook App / faceweb 返回 Error；
- 旧 Reaction browser 不进入名单。

因此旧移动 Web endpoint 不进入正式 fallback。

## 3. Facebook 555 仍存在原生名单实现

当前安装版本：

`com.facebook.katana 555.0.0.49.59`

runtime DEX 中确认存在：

Reaction：

```text
PermalinkReactorsListFragment
TabbedReactorsListFragment
ReactorsV2DataHandler$loadMore
ReactorsOfContentConnection
FeedbackReactorsGraphService
SHOW_REACTORS_LIST
flyout_reactors_list
```

Share：

```text
PermalinkResharesListFragment
PermalinkResharesListComponent
PermalinkResharesListDataFetch
ResharesOfContentFeedQuery
PostActivityHubSharesListQuery
```

因此当前问题不是“Facebook 已删除名单能力”，而是如何可靠进入正确目标帖的原生 surface。

## 4. 当前注册 permalink route

runtime DEX 存在：

```text
fb://feed_story_permalink_non_encoded_id/{id}?open_reactors_list={?open_reactors_list}
```

当前 fallback 组合：

```text
<author_id>_<post_id>
```

Reaction-only 时尝试：

```text
fb://feed_story_permalink_non_encoded_id/<author_id>_<post_id>?open_reactors_list=true
```

Share 或两类同时失败时从普通 permalink surface 起步。

## 5. 2026-08-17 的关键修正

过去文档曾写“复合 ID 能稳定定位正确目标帖”。这个结论已经被 fresh 现场推翻，现正式更正为：

> composite permalink deeplink 只能作为目标定位尝试，不能作为目标身份保证。

真正可信的目标证据是 fallback 起点之后，Reaction / Share family 自己的 GraphQL `source_post_id`。

### Reaction 串帖现场

`runs\20260817-111700`

预期目标：

```text
I Love The Universe
post_id = 1511174857690926
```

fallback Reaction 实际 source：

```text
122272623644144568
```

两者不同，证明 deeplink 可落到别帖。

### Share 串帖与早停现场

`runs\20260817-113005`

预期：

```text
World Truth
post_id = 1557368649518003
```

fallback 第一批 Share GraphQL：

```text
source_post_id = 1357431863226395
```

运行时立即记录：

```text
target_guard.status = mismatch
family = share
expected_post_id = 1557368649518003
observed_source_post_ids = [1357431863226395]
```

并且：

```text
SHARE_SCROLL_FILES = 0
PAGINATION_FILES = 0
```

说明 target mismatch 在 Share 分页前已经被阻断。

## 6. fallback 触发条件

Reaction 工程失败：

```text
query-not-triggered
reaction-list-not-confirmed
no-visible-reaction-count-entry
no-reaction-count-after-comments-normalization
```

Share 工程失败：

```text
no-share-entry
entry-or-query-failed
```

明确禁止：

```text
friends-only-names
query-triggered-friends-only-visibility
visibility-restricted
```

## 7. exact target 的前置解析

fallback 不从 Feed 随机猜目标。

必须先从首轮真实 Share / Reaction query 中得到唯一 `source_post_id`，然后在 analyzer 的帖子记录中找到唯一同 ID 且有 `author_id` 的记录，才生成 composite。

无法唯一确定时不执行猜测性重开，记录：

```text
exact-source-post-id-unavailable
ambiguous-source-post-id
matching-post-author-unavailable
ambiguous-matching-post
```

## 8. fallback 时间窗口

同一 JSONL 中同时存在：

- fallback 前的旧记录；
- fallback 后的新记录。

所以不能只看整份 capture 中有没有目标 post ID。

当前实现记录：

`fallback_start_line`

fallback explorer 接收：

```text
ExpectedSourcePostId
CaptureMinLine = fallback_start_line + 1
```

`facebook_capture_progress.py` 支持：

```text
--min-line
```

并输出 family 的：

```text
observed_source_post_ids
```

这样 target guard 只依据 fallback 之后的新请求，不会被旧目标流量误导。

## 9. 运行时 target guard

`explore_facebook_readonly.ps1` 当前有：

- `target_guard`
- `target_guard_events`

Share：

- share-list 打开并出现新 Share family query 后立即校验；
- mismatch 时 `share_screening.classification=target-mismatch`；
- 不进入 Share 深滚动。

Reaction：

- screening 阶段和 deep pagination 阶段持续校验；
- mismatch 时停止；
- pagination stop reason 可记录 `target-mismatch`；
- mismatch 时不再错误设置 `deep_capture_completed=true`。

## 10. fallback 后的 per-family 最终校验

`run_facebook_capture.ps1` 在 explorer 正常退出后，只分析：

`fallback_start_line + 1` 之后的记录。

分别计算：

```text
reaction_target_validation_status
share_target_validation_status
```

每个 family 的状态：

- `matched`：只观察到一个 source ID，且等于目标；
- `mismatch`：出现了错误或混合 source ID；
- `no-family-query`：fallback 后没有该 family query。

全局 `target_validation_status`：

- 任一 eligible family mismatch -> `mismatch`；
- 无 mismatch 且至少一个 family matched -> `matched`；
- 否则 -> `no-family-query`。

全局状态不能替代 family 状态。

## 11. recovered 的最终规则

`facebook_batch_summary.py` 现在采用保守判断。

Reaction recovered 必须同时满足：

- Reaction fallback eligible；
- 不是 friends-only；
- `reaction_target_validation_status == matched`；
- 最终目标 source 与 fallback 目标一致；
- 有目标 Reaction 用户，或有目标 query 且 fallback reaction-list 已确认。

Share recovered 同理，必须：

`share_target_validation_status == matched`

重要：

- `no-family-query` 永远不能借用 fallback 前的旧目标数据制造 recovered；
- mismatch 永远不能 recovered；
- mismatch fallback pagination 不进入目标帖 summary；
- matched Reaction fallback pagination 才允许并入目标 summary。

## 12. 兼容历史 fallback JSON

较老 run 没有 per-family validation 字段。

当前 summary 对历史记录采用保守兼容：

- global `mismatch` / `no-family-query` 直接作为 family 非恢复依据；
- global `matched` 也不会自动等于 family matched；
- 必须存在该 family 的持久化 `observed_*_source_post_ids` 且唯一等于目标，才会回推 `matched`；
- 否则按 `no-family-query` 或 `mismatch` 处理。

## 13. 当前证据样本

### 正常成功控制样本

`runs\20260816-130059`

首轮已成功，第二层 fallback 不重复触发。

### 工程失败但未恢复

`runs\20260816-130524`

Cosmic Vault：Reaction fallback 真实触发，但二次 surface 仍没有形成可枚举 Reaction 入口，保持 recovered=false。

### Reaction 串帖

`runs\20260817-111700`

用于验证 fallback 时间窗口和 wrong-source 防污染。

### Share 串帖早停

`runs\20260817-113005`

用于验证：

- runtime source mismatch；
- Share 零分页早停；
- final mismatch；
- target summary recovered=false。

## 14. 当前测试

2026-08-17：

```text
Ran 33 tests
OK
```

新增回归覆盖：

- `--min-line` 时间窗口；
- wrong-source family；
- `no-family-query` 不 recovered；
- Reaction matched / Share no-family-query 只恢复 Reaction；
- mismatch pagination 不合并；
- matched fallback pagination 可合并；
- friends-only 不被 fallback 恢复。

PowerShell 核心脚本 parser 全部通过。

## 15. 当前未完成的现场证据

现有真实 runs 中还没有一个第二层 permalink fallback 后，family 新 GraphQL source ID **明确等于目标 post ID** 且最终成功恢复的 fresh 样本。

因此：

- 负向 wrong-post 路径已经 live 闭环；
- no-query / privacy / final filtering 已有回归覆盖；
- 正向 matched 分支已单元测试覆盖；
- 仍需要通过低预算 fresh 候选找到一个真实 `matched` 样本，才能宣称正向 live 闭环。

这不需要放宽 guard，也不需要回退到旧 Web endpoint。

## 16. 后续研究优先级

若继续研究 Facebook 555 原生 navigation，优先：

1. 定位 `open_reactors_list` handler；
2. 确认 composite ID 如何转为内部 feedback/profile list 参数；
3. 查是否存在正式 exported route 直达 `SHOW_REACTORS_LIST`；
4. 查 `PermalinkResharesListFragment` 的 exported navigation；
5. 保持 Facebook 自己构造内部参数，不手工伪造私有 Parcelable。

正式生产判断始终以 family GraphQL `source_post_id` 为准，而不是以 deeplink 是否“看起来打开了一个帖子”作为准则。
