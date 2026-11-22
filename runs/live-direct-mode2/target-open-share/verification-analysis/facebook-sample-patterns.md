# Facebook 样本规律报告

输入：`C:\Users\lenovo\Desktop\facebook\runs\live-direct-mode2\safe-facebook-http-capture.jsonl`
读取记录：51，JSONL 解析失败：0
GraphQL 记录：49，连续 JSON 响应：1
帖子摘要：1，可见转发用户：0，可见互动用户：0
分页对象：8，互动分页请求：0

## 关键查询

- `image`: 9
- `FBAvatarAssetsConfigQuery`: 2
- `AIBotsConsentStateQuery`: 2
- `AutofillFetchContactDataQuery`: 2
- `(unnamed)`: 2
- `StaticGraphQlStoryFeedbackAtConnectionQuery`: 1
- `sendAnalyticsLog`: 1
- `FbStoriesUnifiedSingleBucketQuery`: 1
- `vps_prefetch_DASH_AUDIO`: 1
- `vps_prefetch_DASH_VIDEO`: 1
- `FriendingJewelContentQuery`: 1
- `ThrottledMobileSoftErrorConfigsGraphQL`: 1
- `SfvToReelsBottomsheetUpsellQuery`: 1
- `FxFbConnectedServicesInfoQuery`: 1
- `FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery`: 1
- `MessagingRegionQuery`: 1
- `FxFbLinkageCacheQuery`: 1
- `FeedPositionalSurveyQuery`: 1
- `FxFbFetaInfoQuery`: 1
- `XAVSwitcherFetchDBLFBAccountInfoQuery`: 1
- `AutofillFetchSettingsQuery`: 1
- `AutofillUpdateNegativeInteractionCountMutation`: 1
- `EncryptedBackupsFetchAutoEbUpsellTypeQuery`: 1
- `AutofillFetchContactDataMultipleEntriesQuery`: 1
- `WemPrivateSharingQuery`: 1

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
