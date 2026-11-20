# Facebook 样本规律报告

输入：`C:\Users\lenovo\Desktop\facebook\runs\live-verify-full\safe-facebook-http-capture.jsonl`
读取记录：251，JSONL 解析失败：0
GraphQL 记录：247，连续 JSON 响应：6
帖子摘要：40，可见转发用户：16，可见互动用户：949
分页对象：100，互动分页请求：39

## 关键查询

- `image`: 54
- `FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection`: 37
- `sendAnalyticsLog`: 23
- `rmd-mapfetcher`: 6
- `FbStoriesLightBucketsQuery`: 5
- `NotesBasicQuery`: 4
- `DartsIPAddressSignalProvider`: 4
- `FBAvatarAssetsConfigQuery`: 4
- `AIBotsConsentStateQuery`: 4
- `(unnamed)`: 4
- `vps_prefetch_DASH_VIDEO`: 4
- `vps_prefetch_DASH_AUDIO`: 3
- `fresh_feed_more_data_fetch`: 3
- `FBVideoHomeExternalPostLinkQuery`: 2
- `NotificationsPageApiBadgeCountQuery`: 2
- `fresh_feed_new_data_fetch`: 2
- `FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPScreenQuery`: 2
- `FBApproveFromAnotherDeviceRecentPendingApprovalQuery`: 2
- `XAVSwitcherBadgingDataQuery`: 2
- `FBPrivacyFlowTriggerQuery`: 2
- `StaticGraphQlStoryFeedbackAtConnectionQuery`: 2
- `FetchJewelCountsLiveQuery`: 2
- `BookmarksQuery`: 2
- `TabBadgeQuery`: 2
- `FirstNotificationsPageQueryNewApi`: 2

## 解释

- 信息流和帖子详情使用 GraphQL；部分响应是连续 JSON 文档流。
- 转发用户从 `ResharesOfContentFeedQuery` 的 `reshares.edges[].node.actors[]` 提取。
- 互动用户名单从 `FeedbackReactorsGraphService` 或 Reel 的 `FbShortsIGMediaReactorsQuery` 连接结构提取，并按源帖和用户去重。空边和空节点记录为当前账号可见性限制，不当作失败重试。
- 输出只来自本地抓包，不重放请求，不包含原始 Cookie、Authorization 或 access_token。

## 输出文件

- `facebook-sample-patterns.json`：机器可读汇总。
- `facebook-posts.csv`：帖子摘要。
- `facebook-share-users.csv`：可见转发用户。
- `facebook-reactor-users.csv`：可见互动用户，含 `source_field` 和分页状态；当前样本可能为空。
- `facebook-reaction-counts.csv`：表情计数。
