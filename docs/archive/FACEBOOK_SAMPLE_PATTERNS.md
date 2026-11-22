# Facebook 样本规律报告

样本来源：`facebook-http-capture.jsonl`，共 128 条完整 JSONL 记录。分析输出在 `analysis-current`。

本次样本确认了以下共性：

- 主数据接口集中在 `POST /graphql`，请求通过 `x-fb-friendly-name` 区分用途。
- 信息流响应是连续 JSON 文档流，包含初始数据、延迟 edge 和最终 `page_info`；不能只调用一次普通 JSON 解析。
- `fresh_feed_new_data_fetch` 和 `fresh_feed_more_data_fetch` 返回帖子正文、作者、帖子链接、评论数、分享数和表情计数。
- 打开分享入口后出现 `ResharesOfContentFeedQuery`，返回 `reshares.edges[].node.actors[]` 和 `page_info`。当前历史样本有 7 条可见转发记录，下一页为 false。
- 打开互动入口后出现 `FetchFeedbackReactionsCountsQuery` 和 `FeedbackReactorsGraphService`。前者返回表情计数，后者当前返回空 `reactors.edges`，对应 Facebook 的好友可见性提示。
- 媒体、视频预取、埋点请求数量较多，批量分析必须按 GraphQL friendly name 过滤，不能把所有网络请求当作帖子数据。
- 只有用户节点才能构建互动边；只有计数时不生成用户边。转发边和互动边都按查询中的源 `feedback_id` 关联。本次已从帖子详情/评论响应回查到源帖 `1455106749998706`，7 条转发边均已正确关联。

当前分析输出：

| 项目 | 数量 |
| --- | ---: |
| 有效帖子摘要 | 25 |
| 可见转发用户 | 7 |
| 可见互动用户 | 0 |
| 网络节点 | 32 |
| 网络边 | 7 |

“可见互动用户为 0”不是抓包失败，而是当前账号的响应没有用户节点。后续如果账号对某公开帖有合法的互动用户可见权限，分析器会自动把 `reactors.edges[]` 导出为 `facebook-reactor-users.csv`，网络构建器会生成 `reaction` 边。

## 输出

- `analysis-current/facebook-sample-patterns.json`
- `analysis-current/facebook-posts.csv`
- `analysis-current/facebook-share-users.csv`
- `analysis-current/facebook-reactor-users.csv`
- `analysis-current/facebook-reaction-counts.csv`
- `analysis-current/facebook-network.json`
- `analysis-current/facebook-network-edges.csv`
