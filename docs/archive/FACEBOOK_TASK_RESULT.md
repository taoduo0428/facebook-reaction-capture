# Facebook 两个任务结果

更新时间：2026-08-14

## 结论

基于本地已保存的脱敏解析结果，两个 API 任务都已经定位并验证，当前账号可见范围内的转发用户已经导出。第一个任务的离线解析流程已经完善，并已从“全部”互动分类的 5 个分页响应中解析出 125 名唯一可见用户。Facebook UI 显示该帖共有 1,668 次互动，但后续滚动没有再发出新的分页请求，因此不能把 125 名宣布为 1,668 人的全量名单。

## 任务状态

| PDF 要求 | 当前结果 | 证据 |
| --- | --- | --- |
| 获取公开帖子的互动用户 | 当前账号可见范围已获取 125 名；全量仍未完成 | `FeedbackReactorsGraphService` 共 5 个响应、每个 25 条，去重后 125 名；每页 `has_next_page=true`，但 UI 后续滚动不再触发新请求 |
| 获取公开帖子的转发用户 | 已完成当前账号可见范围 | `ResharesOfContentFeedQuery` 返回 7 条记录，7 个唯一用户，`has_next_page=false` |
| 构建互动/转发网络 | 已生成可见范围网络 | 当前网络有 32 个节点、7 条已关联转发边、0 条互动边 |

## 当前数据

- 抓包记录：最新两批 JSONL 已解析，JSONL 解析错误：0
- 帖子摘要：25 条，其中 22 条包含正文
- 可见转发用户：7 名，用户 ID 和分享故事 ID 均去重
- 互动计数：UI 显示 1,668 次，包含 8 个分类标签（“全部”加表情分类）
- 可见互动用户：125 名（全部分类当前账号可见分页）
- 互动用户全量：未确认；不能用 1,668 次计数推算用户名单
- 网络边：旧样本中的 7 条已关联转发边；最新 125 名互动用户单独保存在 `facebook-reactor-users-20260814-all.csv`

解析器回归测试：3/3 通过，覆盖 `edges`、`nodes`、嵌套用户、`important_reactors`、`friend_reactors`、分页状态和去重。

转发源帖已从同批次的帖子详情/评论响应中回查到：`1455106749998706`，作者为 `South China Morning Post`。该帖正文和计数也已合并进帖子 CSV，因此网络不再使用 `unresolved_source_post=true`。

## 帖子正文示例

来源：`analysis-current/facebook-posts.csv`。以下是本地抓包解析出的公开帖子正文，不是人工编写的样例：

作者：Xi's Moments

> "Friendship, which derives from close contact between the people, holds the key to sound state-to-state relations," President #XiJinping articulated this philosophy at the opening ceremony of the Belt and Road Forum for International Cooperation in 2017. #XiDiplomacy #StoriesaboutXi Throughout his overseas visits and diplomatic engagements, Xi has forged friendships with people around the world through sincerity and mutual respect.

该 CSV 保存了 25 条帖子摘要；正文过长的记录按解析器上限保留前 1000 个字符。

## 7 名可见转发用户

| 用户 | 用户 ID | 转发故事 ID |
| --- | --- | --- |
| Vladimir Tikhonov | 1314211579 | `UzpfSTEzMTQyMTE1Nzk6MTAyNDM3NzU4NzI5OTQ5OTk6MTAyNDM3NzU4NzI5OTQ5OTk=` |
| Saehi Han | 100001035363497 | `UzpfSTEwMDAwMTAzNTM2MzQ5NzoyODI4MzQ2MDc5MTI3MTcyMjoyODI4MzQ2MDc5MTI3MTcyMg==` |
| Piyada Tharachai | 61571053919879 | `UzpfSTYxNTcxMDUzOTE5ODc5OjEyMjE2OTc0MzU0MDcwMTc5NzoxMjIxNjk3NDM1NDA3MDE3OTc=` |
| Hachi Kasuga | 100000985567438 | `UzpfSTEwMDAwMDk4NTU2NzQzODoyODA4MjUwNDgxNDY5ODk5NToyODA4MjUwNDgxNDY5ODk5NQ==` |
| Cassandra Denise Mines | 100000222969923 | `UzpfSTEwMDAwMDIyMjk2OTkyMzoyOTA0NDE1MzkyNTE3NTMyMToyOTA0NDE1MzkyNTE3NTMyMQ==` |
| Mohd Firdaus | 100022750217911 | `UzpfSTEwMDAyMjc1MDIxNzkxMToyMjkxNzY2MDkxNTkxNzA5OjIyOTE3NjYwOTE1OTE3MDk=` |
| Seng Fook Ong | 100001146965788 | `UzpfSTEwMDAwMTE0Njk2NTc4ODoyNzgxNjA3NjI4MTM0NzIzMToyNzgxNjA3NjI4MTM0NzIzMQ==` |

## 互动接口结果

`FetchFeedbackReactionsCountsQuery` 返回 UI 计数，当前帖子显示 1,668 次互动。`FeedbackReactorsGraphService` 的最新抓包返回 5 个分页响应：

```text
每页 `reactors.edges` = 25
5 页合计 = 125 条
按用户 ID 去重 = 125 名
每页 `reactors.page_info.has_next_page` = true
```

当前账号可以看到这 125 名用户的姓名和用户 ID，但 Facebook 没有继续返回后续分页。`facebook-reactor-users-20260814-all.csv` 只代表已实际返回的可见名单，网络生成器不能把剩余计数伪造成用户边。

现在的 `analyze_facebook_capture.py` 会读取以下响应形状：

- `reactors.edges[].node`
- `reactors.nodes[]`
- `important_reactors.nodes[]`
- `friend_reactors[]` 或其连接形式
- 用户被 `actor`、`user`、`profile` 等一层包装的情况

每条导出的互动记录还保留 `source_field` 和分页状态，便于追溯来源；同一帖子同一用户跨页重复出现时只保留一条。

## 输出文件

- `analysis-current/facebook-posts.csv`：25 条帖子摘要和正文
- `analysis-current/facebook-share-users.csv`：7 名可见转发用户
- `analysis-current/facebook-reactor-users.csv`：旧样本的互动用户结果
- `analysis-current/facebook-reactor-users-20260814-all.csv`：最新全部分类可见互动用户，125 名
- `analysis-current/facebook-reactor-users-20260814-all.json`：最新分页范围和完整性状态
- `analysis-current/facebook-reaction-counts.csv`：7 类表情计数
- `analysis-current/facebook-network.json`：网络节点和边
- `analysis-current/facebook-network-edges.csv`：网络边明细
- `analysis-current/facebook-network.dot`：Graphviz 网络图源文件
- `analysis-current/facebook-sample-patterns.json`：解析统计和分页证据

网络中的 7 条转发边都已连接到源帖 `1455106749998706`；源帖来自同批次详情响应，不是根据用户名或时间猜测关联。

## 社区检索结论

- Meta 官方 Post Reactions 文档描述返回 `Profile` 节点、分页和 `summary.total_count`，但实际数据仍受 viewer 权限和隐私范围影响。
- `www10177/Facebook-Reaction-Scraper` 使用 Selenium 读取登录账号在网页上实际能看到的 reaction 列表并导出 CSV；它不是 GraphQL 权限绕过方案。
- 社区工具可以在“网页确实显示名单”的账号上帮助采集，但不能保证当前账号看到全部互动者。当前抓包的 `reactors.edges=[]` 与网页可见性限制一致。

## 数据边界

这些结果只代表当前账号、当前时间和当前帖子范围内 Facebook 返回的可见数据。没有重放请求、伪造 cursor、绕过好友可见性或把计数转换成用户名单。若要继续逼近全量，必须让 Facebook UI 对每个表情分类实际发出后续分页请求，并对所有响应按用户 ID 去重；如果 UI 不再发请求，任务只能交付为“当前账号可见范围”，不能声称 100% 全量。
