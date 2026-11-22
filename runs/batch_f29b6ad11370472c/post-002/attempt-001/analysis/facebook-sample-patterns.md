# Facebook 样本规律报告

输入：`C:\Users\lenovo\Desktop\facebook\runs\batch_f29b6ad11370472c\post-002\attempt-001\safe-facebook-http-capture.jsonl`
读取记录：93，JSONL 解析失败：0
GraphQL 记录：91，连续 JSON 响应：2
帖子摘要：3，可见转发用户：0，可见互动用户：50
分页对象：36，互动分页请求：3

## 关键查询

- `sendAnalyticsLog`: 15
- `FbStoriesLightBucketsQuery`: 4
- `vps_prefetch_LIVE_AUDIO`: 4
- `vps_prefetch_LIVE_VIDEO`: 4
- `vps_prefetch_DASH_AUDIO`: 3
- `FBVideoHomeExternalPostLinkQuery`: 2
- `StaticGraphQlStoryFeedbackAtConnectionQuery_At_Connection_Pagination_Feedback_comments_paginating`: 2
- `fresh_feed_new_data_fetch`: 2
- `image`: 2
- `FBScreenTimeSyncMutation`: 2
- `NotesBasicQuery`: 2
- `FBAvatarAssetsConfigQuery`: 2
- `AIBotsConsentStateQuery`: 2
- `(unnamed)`: 2
- `FeedbackReactorsGraphService`: 2
- `vps_prefetch_DASH_VIDEO`: 1
- `NotificationsPageApiBadgeCountQuery`: 1
- `FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPScreenQuery`: 1
- `FBApproveFromAnotherDeviceRecentPendingApprovalQuery`: 1
- `XAVSwitcherBadgingDataQuery`: 1
- `StaticGraphQlStoryFeedbackAtConnectionQuery`: 1
- `FBPrivacyFlowTriggerQuery`: 1
- `FetchJewelCountsLiveQuery`: 1
- `TabBadgeQuery`: 1
- `FirstNotificationsPageQueryNewApi`: 1

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
