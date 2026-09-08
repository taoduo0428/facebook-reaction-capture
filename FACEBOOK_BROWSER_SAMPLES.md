# 浏览器与本地采集实测样本（2026-09-08）

用户指定的已登录 Edge 账号，人工式页面点击验证。仅记录公开帖子地址和可见性结论，不保存登录材料或用户姓名。以下不等于 Android/Mac 完整采集验收。

| 帖子 | 页面观察 | 验收用途 |
| --- | --- | --- |
| https://www.facebook.com/watch/?v=1069930052107630 | Japan Experience，Yabusame 视频；All 334、Like 279；打开 Like 名单后显示多条真实用户条目 | 名单可见正例，尚需 Android 严格目标与分页终止验证 |
| https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/ | Chinese and South East Asian Cooking 公开群组，“Simple Chinese food”；首次打开 Reaction 名单观察到真实用户条目；随后无参数帖子地址复核显示总数 266、Like 228 people。后一次快照中的用户条目为评论，不作为名单证据 | 群组名单可见正例依据首次名单观察；无参数地址可访问；仍需 Android 严格目标与分页终止验证 |
| https://www.facebook.com/scmp/posts/pfbid02cwtTB6SMNxjqvbB8BhfET3KQrJZQSjxnbzxfE1LZXMT2EtN3vPK78zvvJVnPVe9il | SCMP Dairy Queen 图文帖；All 63；明确提示仅显示已点赞好友姓名 | 可见性受限负例，不得按总数宣称完整名单 |
| https://www.facebook.com/chinadaily/posts/pfbid0BLXGZ4puu1o5igYonBkhW3Zo1U1LqgELJhoU9iWorfhYuTSdvbck6c1sM2jKoHZVl | China Daily 图文帖；All 1148；同样仅显示好友姓名 | 可见性受限负例 |

## 2026-09-08 本地 Android 首页探测

以下探测使用现有 `worker-01 / emulator-5554`，每个链接只打开首个 Reaction 列表，不翻第 2 页、不遍历分类。`initial-ready` 表示本次运行确认了目标帖子并读到首屏可见用户；它不代表已经抓完全部用户。

| 输入 URL | 结果 | 证据 |
| --- | --- | --- |
| `https://www.facebook.com/61563119350848/posts/122106880826437311` | `initial-ready`，严格目标确认，首屏 31 个可见用户，无限流 | `runs/20260908-221817`；目标 post `122106880826437311`，Reaction 总数 3537 |
| `https://www.facebook.com/share/p/1DVLR4FNo6/` | `initial-ready`，短链解析到 `story.php`，首屏 26 个可见用户，无限流 | `runs/20260908-222003`；解析 post `1691321539667513`，Reaction 总数 949 |
| `https://www.facebook.com/share/r/1Dg59X6ysJ/` | `initial-ready`，短链解析到 Reel `1054985397317048`，首屏 2 个可见用户，无限流 | `runs/20260908-223036`；本次新鲜查询绑定 source post `122122050735339946` |
| `https://www.facebook.com/watch/?v=1069930052107630` | 严格目标确认失败，3 次均拒绝继续；未导入其他帖子数据 | `runs/20260908-222131`；当前 Watch URL 在移动抓包中没有形成可确认目标 |
| `https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/` | 目标 ID 已确认，但移动端没有渲染可定位的 Reaction 总数入口，程序安全停止 | `runs/20260908-222458`；未盲目上滑，未产生用户名单 |
| `https://www.facebook.com/scmp/posts/pfbid02cwtTB6SMNxjqvbB8BhfET3KQrJZQSjxnbzxfE1LZXMT2EtN3vPK78zvvJVnPVe9il` | 3 次严格目标确认失败；浏览器侧同时观察到“只显示已点赞好友”限制 | `runs/20260908-222728`；不能把页面总数当作完整名单 |

数量与可见性可能随时间和登录账号变化。新加坡接收者需要用自己的账号重新验证；不同地区无需 Clash，不意味着账号具有相同名单可见权限。浏览器能显示 Reaction 名单，也不自动证明 Android 采集器可以分页；正式全量任务仍需目标确认、分页终止和完整性检查全部通过。
