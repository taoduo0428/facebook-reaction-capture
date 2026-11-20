# 实习工作梳理：DRV 与数据爬虫

> 更新提示（2026-09-09）：本文中的工作量和测试数字按原统计日期保留。Facebook 当前实现与真实运行状态见 `CURRENT_STATUS_2026-09-09.md`，不要把后续 `initial-ready` Probe 写成全量完成。

更新时间：2026-09-01  
工作归属：仅统计 Git 身份 `taoduo` 以及现有爬虫工作区中可核验的本人工作。  
排除项：LeapLite 的 DRV 侧边栏按钮提交作者为 `keyifei`，不计入本文。

## 一、工作概览

入职实习以来，我的工作主要分为两条主线：

1. 参与并完成 LeapAgent 的 DRV V2 后端研发，把工作过程和交付物转换成可审计、可重复计算的 DRV 结果。
2. 搭建并完善外包价格、招聘岗位和 Facebook 互动用户爬虫，为 DRV 市场定价和传播关系分析提供真实数据。

这两条主线并不是单纯调用模型或运行现成脚本。实际工作包括需求和口径梳理、数据源调研、后端编码、模型输入输出约束、浏览器及网络链路调试、断点和失败恢复、数据清洗、测试、真实样本验证以及交付文档整理。

## 二、DRV V2 后端研发

### 2.1 项目是做什么的

DRV（Daily Result Value）用于根据一天内完成的工作和实际交付物，对工作价值进行结构化评估和计算。

旧方案容易把语义判断、价格判断和最终金额都交给模型，结果可能受提示词、模型输出格式或随机性影响。DRV V2 的目标是把职责拆开：

- 模型负责识别工作项和交付物，并判断职业、等级、任务规模、完成度、质量和 AI 贡献等语义字段。
- Go 后端负责校验字段、读取可信市场价格、执行确定性计算、保存进度和生成报告。
- SQLite 负责保存历史完成度、评估结果、交付状态和定时任务状态，使重复运行和失败重试可控。

### 2.2 我负责的具体工作

#### 1. 设计并实现确定性 DRV 计算链路

我实现了从会话证据到最终 DRV 报告的主要后端流程，包括：

- 收集会话、工具调用、结果文件等工作证据；
- 让模型把证据拆分为 work item 和 deliverable；
- 将职业、等级、任务规模、完成度、质量、AI 贡献等字段转换为结构化结果；
- 根据版本化市场费率和固定 BPS 档位计算金额；
- 生成包含计算依据、费率版本和输入快照的 DRV 报告；
- 将报告和进度写入 SQLite。

最终金额不由模型直接生成。模型不能输出或决定最终 DRV、时薪和市场系数，金额由 Go 代码使用分、BPS 和整数运算计算，从而保证同样输入可以得到同样结果。

主要代码范围包括：

- `internal/drv/assessment.go`
- `internal/drv/calculation.go`
- `internal/drv/v2.go`
- `internal/drv/marketdata.go`
- `internal/drv/collector.go`
- `internal/drv/reporter.go`
- `internal/drv/service.go`
- `internal/store/sqlite/drv_v2.go`

#### 2. 建立可信、版本化的市场价格数据

我把职业分类、等级、任务规模、费率和市场系数做成版本化数据，而不是把价格直接写进提示词或让模型自由生成。

具体包括：

- 职业池和职业等级规则；
- level scheme 和 size 规则；
- 不同职业、等级的市场费率；
- 市场数据 manifest 和 SHA-256 完整性校验；
- 报告中保存实际使用的数据版本和费率快照；
- 按报告日期检查市场数据的生效时间，避免重试时误用未来版本。

这部分市场价格数据与爬虫项目有关：爬虫提供外包预算和岗位薪资样本，DRV 后端使用经过确认和版本化的数据，而不是在运行时直接依赖网站或模型猜测。

#### 3. 收紧模型评估边界

我处理了模型输出中比较容易出错的情况，并明确哪些错误可以降级、哪些必须失败：

- 明确的 `work_items: []` 可以表示当天没有可计价交付物；
- 缺失或为 `null` 的 `work_items` 仍属于格式错误，不能当成零工作；
- 非法职业、等级、任务规模或定价方式在有界重试后可以进入 `pending_review`；
- `pending_review` 会保留交付物和审计信息，但不会计算非零金额；
- 非法完成度、质量或 AI 贡献 BPS 仍然严格失败；
- 模型只能从后端提供的可信枚举中选择职业、等级、规模和 BPS，不能自行创造标签；
- 会话证据与可信系统枚举分开传递，避免不可信内容覆盖系统规则。

这项工作的重点不是让更多数据“勉强算出结果”，而是在可审核和不误算之间建立边界。

#### 4. 处理历史进度、重复计算和跨日问题

DRV 需要识别一个交付物以前完成了多少，本次只计算新增完成度。我实现或完善了：

- deliverable identity 和历史完成度查询；
- 增量完成度计算；
- 完成度回退保护；
- 同一交付物的重复识别和防重复计价；
- 按原报告日期收集数据，避免午夜后的重试混入第二天内容；
- legacy、shadow 和正式 V2 路径隔离。

#### 5. 完善可靠交付和定时调度

我补充了 DRV 报告的持久化交付状态和定时任务防并发机制，包括：

- 发送前保存精确 report body 和 report hash；
- 区分 pending、远端已接收、最终完成和失败状态；
- 失败后可以继续发送已有报告，不需要重新调用模型评估；
- scheduled run 使用 lease、heartbeat 和 fencing；
- 防止旧执行器在租约失效后覆盖新执行器状态；
- 记录 attempt count 和原始 scheduled time；
- catch-up 只处理符合条件的历史任务。

#### 6. 编写测试、真实数据验证和研发文档

我不仅写了单元测试，还使用真实开发数据库的只读副本做过 ModeShadow 灰盒验证。

当时验证了 3 个真实会话：

- 只有简短输入、没有交付证据：0 DRV；
- 用户提出写文件但没有成功结果：0 DRV；
- 有成功文件写入证据，但分类仍需审核：保留 1 个 `pending_review` 交付物，0 DRV。

这些结果说明系统没有把普通对话、未完成请求或证据不足的工作强行算成正数。

我还补充了 `docs/drv.md`，记录计算公式、模式区别、市场数据、持久化表、交付状态、调度和测试入口，便于后续开发人员理解和维护。

### 2.3 DRV 的量化结果

- 最终合并到主线的 DRV V2 版本：39 个文件，新增 22,757 行，删除 245 行。
- 早期确定性 V2 主实现：31 个文件，新增 19,300 行。
- 可靠交付和调度风险修复：13 个文件，新增 1,385 行。
- 2026-09-01 现场执行 `go test ./internal/drv`：251 个测试通过。
- 完成 3 个真实 SQLite 会话的 ModeShadow 隔离回放。

上述代码量用于说明工作范围，不等同于个人绩效或代码质量；测试结果只证明当前 `internal/drv` 包的本地测试通过，不代表所有线上业务场景都已覆盖。

## 三、外包价格与招聘岗位爬虫

### 3.1 项目是做什么的

这个项目用于收集外包任务价格和招聘岗位薪资，为 DRV 市场价格表、职业分类和后续数据分析提供样本。

项目覆盖两类数据：

- 外包与自由职业价格：一品威客、猪八戒、实现网、程序员客栈；
- 招聘岗位和薪资：BOSS 直聘、前程无忧、猎聘。

不同平台的数据结构和访问方式差异很大，因此不能使用一个通用选择器完成。实际采用了静态 HTTP、SSR/Nuxt 数据解析、Playwright、Chrome CDP、页面原生请求、登录态复用和详情补采等多种方式。

### 3.2 我负责的具体工作

#### 1. 调研网站数据结构和业务口径

我先确认每个平台的“价格”具体代表什么，避免把不同业务含义混在一起：

- 一品威客和猪八戒主要是任务预算或服务套餐价格；
- 实现网同时包含平台估价、一般月薪和雇主正文价格；
- 程序员客栈更接近人才日薪或月度服务报价；
- 招聘网站主要是岗位月薪、年薪、日薪及发薪月数。

原始价格文本、列表价格、详情价格、平台估价和正文价格分别保存，不用一个字段互相覆盖。区间、周期和币种信息也保留，防止后续分析时失去来源语义。

#### 2. 搭建统一爬虫框架

我实现或完善了：

- 统一配置、站点适配器和命令行入口；
- 统一的 `CrawlRecord` 数据结构；
- HTTP 超时、有限重试、退避、`Retry-After` 和单域限速；
- 页面、记录、详情请求和响应大小上限；
- Cookie/storage state 域名和有效期过滤；
- 登录墙、WAF、429、403 和结构变化的显式失败；
- SQLite/JSONL 断点、去重和重复运行保护；
- JSONL、CSV、XLSX 和质量报告导出；
- 临时目录写入完成后再替换正式结果；
- 测试夹具、解析测试、失败路径测试和数据打包校验。

#### 3. 实现三站招聘采集链路

BOSS、前程无忧和猎聘分别使用不同方案：

- BOSS：隔离 Chrome + CDP，读取官方职业分类并监听页面原生岗位响应；
- 前程无忧：从页面读取官方三级职能树，在页面环境中调用带签名的岗位接口；
- 猎聘：读取官方 `/career/` 三级职业目录，抓取职业页岗位卡，再单独补采详情 JD。

我还实现了统一调度、分类进度、冷却状态、失败预算和恢复机制。遇到安全验证或登录失效时停止并保存进度，不把验证页误判为空分类，也不无限重试。

#### 4. 完成招聘数据清洗和交付

完整元数据保留原始嵌套字段；最终数据提取职位、JD、薪资和三级官方分类。清洗时处理 HTML、控制字符、空白和换行，但不改写源站薪资。

最终落盘结果：

| 平台 | 原始数据 | 最终清洗数据 | 主要处理 |
| --- | ---: | ---: | --- |
| 前程无忧 | 27,552 | 27,552 | 使用搜索响应中的完整 JD |
| 猎聘 | 49,056 | 46,607 | 补采 46,607 条有效详情 JD，排除 2,449 条无 JD 记录 |
| BOSS | 40,750 | 39,594 | 保留完整元数据，最终排除 1,156 条无可用 JD 记录 |
| 合计 | 117,358 | 113,753 | 输出 JSONL、CSV、XLSX |

处理脚本会重新读取三种输出格式，核对行数、重复 ID、缺失字段、元数据保留数量和 Excel 截断情况。

#### 5. 完成外包价格数据交付

旧版正式交付包含三个平台：

| 平台 | 有效交付数量 | 数据口径 |
| --- | ---: | --- |
| 猪八戒 | 1,668 | 有明确预算的任务，另过滤 5,414 条“待服务商报价”记录 |
| 一品威客 | 2,691 | 任务、三级分类和价格 |
| 实现网 | 53 | 34 条严格三个月内，19 条“3个月前”边界记录 |
| 合计 | 4,412 | 三个平台正式结果 |

程序员客栈已经实现站点策略，但当前正式结果目录没有对应的大规模交付文件，因此只能写“已实现采集策略”，不能与上述 4,412 条交付量合并。

## 四、一品威客近三个月专项补充

### 4.1 项目目标

旧版一品威客数据已有任务标题、分类和价格，但任务描述、交易模式、详情字段和价格可信度还不足以直接用于任务分析或价格建模。

这次专项工作的目标是补齐详情、核验近三个月范围，并形成分层的数据集，而不是简单增加采集数量。

### 4.2 我做的具体工作

- 审计旧版 2,691 条正式结果和未解决详情；
- 检查列表、详情页、Nuxt `taskData` 和 12 个 Sitemap 分片；
- 保留任务 ID、标题、描述、交易模式、预算、雇主、分类、截止时间和中标信息；
- 不把 Sitemap `lastmod` 直接当作任务发布时间；
- 对详情 403、404、解析失败和字段缺失分别记录；
- 对失败详情进行有界重试和浏览器复核；
- 合并不同批次并按 `record_id` 去重；
- 调用 ChatGPT 按统一审核口径逐条做语义审核；
- 保存每条评分、标签、价格可信度和审核理由；
- 输出完整审计版、高质量版、价格建模版和精简交付版。

需要特别说明：交付文件中的字段名称沿用了“人工等级”，但实际是 ChatGPT 按统一规则逐条语义审核，不应对外描述为本人手工阅读并打分 1,997 次。

### 4.3 量化结果

| 指标 | 数量 |
| --- | ---: |
| 近三个月候选池 | 2,121 |
| 成功获取详情 | 2,050 |
| 未解决详情 | 71 |
| 招标/雇佣主样本 | 1,997 |
| 有完整任务描述 | 1,991 |
| 审核批次 | 80 |
| A 级 | 677 |
| B 级 | 523 |
| C 级 | 424 |
| D 级 | 373 |
| A+B 高质量主数据 | 1,200 |
| 高或中价格置信度 | 956 |
| 暂排潜在重复后的保守建模数据 | 920 |
| 高价格置信度样本 | 408 |

这项工作的真实边界是：1,997 条主样本全部有审核记录，但最初 2,121 条候选中仍有 71 条详情未解决，因此不能说候选池已经 100% 完整详情化。

## 五、Facebook 帖子互动用户爬虫

### 5.1 项目是做什么的

项目用于采集当前登录账号能够看到的 Facebook 帖子 Reaction 和 Share 用户，保存用户 ID、用户名、头像、分页和来源帖子证据，后续可以构建点赞网络、转发网络和传播关系图。

Facebook App 使用 Android 原生网络、GraphQL 和动态 UI，普通网页请求无法稳定完成这项工作。因此项目同时涉及 Android 模拟器、代理、抓包、GraphQL 分析、UI 自动化和任务服务化。

### 5.2 我负责的具体工作

#### 1. 搭建并修复 Android 抓包环境

- 配置 Android 15 模拟器和 ADB；
- 处理 Clash、TUN、HTTP 代理和原生 SecureTCP 的网络路径；
- 使用 mitmproxy/Burp 验证代理可达性；
- 配置模拟器到宿主机端口的转发和恢复；
- 处理模拟器离线、黑屏、ANR、代理丢失和 Facebook 重启；
- 将正常使用代理和临时抓包代理分开，抓包结束后恢复正常网络。

#### 2. 分析 GraphQL Reaction/Share 数据

- 识别 `FeedbackReactorsGraphService` 和 `ResharesOfContentFeedQuery`；
- 提取用户 ID、用户名、头像和 reaction 类型；
- 解析 `page_info`、`end_cursor` 和 `has_next_page`；
- 验证 page2 cursor 与 page1 返回值的连续性；
- 区分 Reaction 用户、Share 用户和页面显示的总计数；
- 保存脱敏后的协议和分页证据，不落盘完整认证材料和原始 cursor。

#### 3. 防止串帖和错误成功

我增加了来源帖子校验：

- 校验 canonical URL、post ID 和 `source_post_id`；
- 目标帖子无法确认时宁可失败，也不接受 feed 中其他帖子的响应；
- 不把只有计数、没有用户节点的响应当成采集成功；
- 不把到达程序 cap 当成 Facebook 已经没有下一页；
- 只有明确观察到 `has_next_page=false` 才可以标记 `terminal=true`。

#### 4. 实现分页、进度和失败恢复

- 去除早期约 200 人的程序上限；
- 支持 1,000+ 和 3,000+ 用户深分页；
- 实时输出分页次数、增长人数、当前用户数和终止原因；
- 加入 loader 检测、native scroll、burst pipeline 和 backpressure；
- 区分 terminal、partial、absolute-cap、session restriction 和 rate limit；
- 保存运行目录、分析结果和可恢复状态。

#### 5. 实现本地异步 API 和任务队列

本地 API 支持：

- 创建抓取任务；
- 查询任务状态；
- 获取最终结果；
- 取消排队或运行中的任务；
- API 重启后保留 completed、failed 和 cancelled 状态；
- 单 Android worker 串行执行，防止多个任务争抢同一个模拟器。

### 5.3 历史真实验证结果

历史 API 全链路验收中：

- 采集 Reaction 用户 3,209 人；
- 唯一用户 ID 3,209 个；
- 用户名覆盖 3,209/3,209；
- 头像覆盖 3,209/3,209；
- 错误 `source_post_id` 数量为 0；
- 最终 `terminal=true`；
- 该轮没有观察到 rate limit 或认证 HTTP 错误。

另一轮 terminal 抓取结果：

- 3,187 个唯一用户；
- 129 个 Reaction 页面/记录；
- 前 127 页每页 25 人，最后一页 12 人；
- 最后一页明确 `has_next_page=false`；
- 总时长约 12.4 分钟；
- 401/403/429 均为 0。

这些结果证明历史上跑通过当前账号可见范围内的 3,000+ 深分页和 API 全链路，但不能写成“抓到了 Facebook 平台全部互动用户”。

### 5.4 当前限制

- `terminal=true` 只表示当前账号、当前 App 入口下可枚举的列表已经结束；
- UI 总数与可枚举身份可能存在少量差异；
- Share 的可见用户名单可能明显少于页面显示的分享总数；
- Facebook 会话可能返回 `1675004`、`3252001` 或 API 368 等限制；
- 当前没有新的有效 10,000 人候选全链路结果；
- 2026-09-01 现场运行 197 个 Facebook 单元测试，有 4 个 release-gate 相关失败，不能描述为当前全量测试通过。

## 六、可以概括成的 5 件实际工作

如果需要向带教、面试官或简历撰写者说明，可以直接概括为：

1. 在 LeapAgent 中开发 DRV V2 确定性计算后端，将模型语义评估、版本化市场价格和 Go 金额计算分离，并完成 SQLite 持久化和报告生成。
2. 完善 DRV 的模型输出校验、`pending_review` 审核机制、增量完成度、重复计价保护、可靠交付和定时任务防并发机制。
3. 搭建外包价格与招聘岗位多站点爬虫，覆盖 7 个站点策略，完成 117,358 条招聘原始数据和 113,753 条清洗数据交付。
4. 对一品威客近三个月任务进行详情补采、合并去重和模型语义审核，从 1,997 条主样本中形成 1,200 条高质量任务数据和 956 条价格建模优先数据。
5. 开发 Facebook Android 互动用户采集系统，完成代理抓包、GraphQL 深分页、防串帖、实时进度、异步 API 和历史 3,000+ 用户真实链路验证。

## 七、目前没有可靠数字支持的说法

以下表述目前缺乏统一基线或标注集，不建议直接使用：

- “DRV 准确率提升了多少百分比”；
- “整体研发效率提高了多少百分比”；
- “为公司节省了多少人天或成本”；
- “爬虫覆盖了各网站全部数据”；
- “所有站点现在都可以稳定在线运行”；
- “Facebook 可以抓到平台全部用户”；
- “1,997 条一品威客数据全部由本人手工审核”。

可以使用的更准确表述是：系统实现了确定性计算、失败保护、断点恢复和可审计数据处理；已经产生明确数量的真实交付数据，但外部网站访问完整性和业务准确率仍需要独立评价。

## 八、证据位置

### DRV

- 仓库：`C:\Users\lenovo\Desktop\leap\DRV研发\LeapAgent`
- 实现说明：`docs/drv.md`
- 核心代码：`internal/drv/`
- SQLite 实现：`internal/store/sqlite/drv_v2.go`
- 主线合并提交：`64e1ffe feat(drv): 增加 v2 确定性计算与运行时支持 (#231)`

### 外包与招聘爬虫

- 项目：`C:\Users\lenovo\Desktop\外包+岗位爬虫`
- 总体说明：`README.md`
- 招聘处理统计：`outputs/招聘--最终结果/处理统计.json`
- 外包结果说明：`outputs/外包--最终结果/说明.md`
- 一品威客最终统计：`outputs/epwk-current-3m-20260820-v3/最终交付/summary.json`
- 一品威客交接说明：`docs/一品威客补充版交接说明.md`

### Facebook

- 项目：`C:\Users\lenovo\Desktop\facebook`
- 总交接报告：`2026-08-15_handoff-facebook-report.md`
- 主分析程序：`analyze_facebook_capture.py`
- 抓取入口：`run_facebook_capture.ps1`
- 异步 API：`facebook_capture_api.py`
- 队列入口：`run_facebook_post_queue.ps1`

## 九、DRV 代码级工作明细

这一节不再用“负责某模块”的概括，而是逐项说明原问题、我实际写了什么、代码落在哪里，以及如何验证。

### 9.1 三种运行模式及兼容策略

我没有直接删除或替换旧 DRV，而是保留了三种模式：

| 模式 | 正式输出 | V2 是否运行 | 进度写入位置 | 目的 |
| --- | --- | --- | --- | --- |
| `legacy` | 旧版报告 | 否 | 旧路径 | 保持现有生产行为不变 |
| `shadow` | 仍是旧版报告 | 是，仅生成对照结果 | 独立 shadow 表 | 用真实数据观察 V2，不污染正式金额 |
| `v2` | V2 报告 | 是 | 正式 V2 表 | 启用确定性计价链路 |

具体实现位于 `internal/drv/types.go`、`internal/drv/service.go` 和 `internal/config/config.go`。

这项工作的实际价值是支持灰度验证：V2 评估失败时，shadow 模式只记录失败，不会把错误 V2 结果替换成正式 legacy 报告；shadow 产生的完成度也不会在以后切换到 V2 时被当成已经正式确认的进度。

### 9.2 从真实会话中收集证据

原问题是模型可能根据对话文字自行推断“文件已生成”或“命令已成功”。我把事实证据收敛到 Collector：

1. 按 `drv.user_id` 查询指定用户的消息、统计和 subagent 数据；
2. 从真实 tool trace 和工具结果中提取文件、命令和执行状态；
3. 为证据分配 ID，生成 `SessionSummary` 和 `EvidenceRef`；
4. Assessment 只能引用 Collector 已经提供的 evidence ID；
5. 模型引用不存在的 evidence 或 session 时，后端校验失败；
6. Store 不支持用户范围查询时直接失败，不退化为读取全部用户数据。

对应代码是 `internal/drv/collector.go` 和 `internal/drv/types.go`。这既是准确性问题，也是多用户数据隔离问题。

### 9.3 模型调用的输入和输出约束

模型调用并不是简单发送聊天记录。我对 Assessment 请求做了以下约束：

- System Prompt 中放入后端从 MarketData 生成的可信枚举；
- 会话内容作为不可信输入单独传递，不能覆盖可信规则；
- occupation、level、size、pricing basis 和 BPS 只能从可信枚举选择；
- 模型只输出 work item、deliverable、语义维度和证据引用；
- 禁止模型输出最终金额、时薪、标准工时、市场系数和历史完成度；
- 解析时区分字段缺失、字段为 `null`、字段类型错误和字段值不在枚举中；
- 调用失败或格式非法时只进行有界重试，不无限调用模型；
- 对模型摘要和证据文本做敏感信息、用户目录路径等内容的清理。

具体实现集中在 `internal/drv/assessment.go`，对应测试在 `internal/drv/assessment_test.go`。

### 9.4 为什么有些错误进入 pending_review，有些直接失败

我把错误分为“可以留下待审核记录”和“不能继续使用”两类：

| 错误 | 最终处理 | 原因 |
| --- | --- | --- |
| 明确 `work_items: []` | 合法零交付 | 允许当天没有可验收成果 |
| `work_items` 缺失或为 `null` | 重试后失败 | 不能把损坏响应伪装成零工作 |
| 非法 occupation/level | 重试后 `pending_review` | 保留交付物，但找不到可信费率，金额必须为 0 |
| 非法 size/pricing basis | 重试后 `pending_review` | 不能确定标准工时或计价方式 |
| 非法 completion BPS | 严格失败 | 直接影响本次新增完成度 |
| 非法 quality BPS | 严格失败 | 直接影响金额 |
| 非法 AI contribution BPS | 严格失败 | 直接影响金额 |
| direct quote 无可信审批来源 | `pending_review` | 防止用报价路径绕过职业池和费率规则 |
| 引用不存在的 evidence | 严格失败 | 不能用虚构证据计价 |

对应的专项提交是 `04d3f92 fix(drv): improve assessment validation and trusted dimensions`，只修改 `assessment.go` 和 `assessment_test.go`，新增 337 行、删除 28 行。修改重点是收紧边界，而不是扩展其他 DRV 行为。

### 9.5 确定性计算的具体规则

真正进入金额计算的是 `DRVDeliverable`，而不是聊天消息、工具调用或整个 Work Item。

标准工时由 size 固定映射：

| size | 标准工时 |
| --- | ---: |
| XS | 1 小时 |
| S | 2 小时 |
| M | 4 小时 |
| L | 8 小时 |
| XL | 16 小时 |
| XXL | 32 小时 |

BPS 只能使用固定档位：

- 完成度：0、2500、5000、7500、10000；
- 质量：6000、8000、10000、12000；
- AI 贡献：0、2500、5000、7500、10000。

概念公式是：

```text
完整交付市场价值
= 职业职级时薪 × 标准工时 × 市场交付系数

本日新增 DRV
= 完整交付市场价值
× (当前累计完成度 - 历史已确认完成度)
× 质量系数
× AI 贡献比例
```

代码中统一使用：

- `fen` 作为金额单位；
- BPS 作为比例单位；
- `big.Int` 保存中间乘积；
- 最终统一 round-half-up；
- 完整原始分子/分母用于最终金额计算，避免多次中间四舍五入。

底层代码位于 `internal/drv/calculation.go`。`CalculateDRV` 会再次校验 size、BPS、费率和完成度，不依赖模型遵守提示词。

### 9.6 市场数据具体如何管理

市场数据不是单个价格表，而是一套有版本关联的数据包：

```text
internal/drv/marketdata/
  manifest.json
  versions/2026-07-27.v5.json
  taxonomies/2026-08-03.v1.json
  occupation_pools/2026-08-03.v1.json
  level_rules/2026-08-03.v1.json
  level_schemes/2026-08-03.v1.json
```

我实现的检查包括：

- manifest 指向的数据文件必须存在；
- 文件内容通过 SHA 校验；
- occupation、level 和 rate 必须互相匹配；
- 报告日期不能早于费率版本生效日期；
- 报告保存 `rate_version`、配置 bundle SHA 和实际费率快照；
- 运行时可以使用内置数据，也可以通过 `drv.market_data_dir` 指向外部版本目录；
- 数据不完整或版本不匹配时 fail closed，不使用默认猜测价格。

实现位于 `internal/drv/marketdata.go` 和 `internal/drv/marketdata_embed.go`。

### 9.7 跨日进度和防重复计价

同一个交付物可能跨多天完成。例如第一天完成 25%，第二天累计到 75%。第二天只能计算新增的 50%，不能再次计算前一天的 25%。

我为此实现了两层标识：

- deliverable ID：本次评估生成的交付物标识；
- `identity_key`：跨日识别同一语义交付物的稳定键。

处理逻辑包括：

1. 读取历史已确认完成度；
2. 计算 `current - previous`；
3. 当前完成度低于历史完成度时标记 `correction_required`，不产生负 DRV；
4. identity 与 occupation 冲突时拒绝静默合并；
5. 只有报告成功持久化和提交后才推进正式 progress；
6. shadow 使用单独的 progress 和 identity 表。

### 9.8 SQLite 中实际增加的状态

| 表 | 保存内容 |
| --- | --- |
| `drv_v2_schema_migrations` | V2 schema 迁移版本 |
| `drv_v2_reports` | pending/submitted V2 报告 |
| `drv_v2_recognitions` | 每个 deliverable 的识别、金额和计算快照 |
| `drv_v2_progress` | 正式 deliverable 完成度 |
| `drv_v2_identity_progress` | 正式 identity 完成度和 occupation 绑定 |
| `drv_v2_evidence` | 报告实际使用的 evidence 快照 |
| `drv_v2_shadow_progress` | shadow deliverable 完成度 |
| `drv_v2_shadow_identity_progress` | shadow identity 完成度 |
| `drv_deliveries` | legacy/shadow 的可靠交付状态 |
| `drv_scheduled_runs` | 定时任务状态、租约和 fencing token |

这些表不是只负责“存一份报告”，而是支持断电恢复、重复请求幂等、跨日进度和并发控制。

### 9.9 报告交付失败后如何恢复

legacy/shadow 的可靠交付状态为：

```text
pending -> remote_accepted -> finalized
```

`StoredDRVDelivery` 保存原始 `report_json`、`delivery_key` 和 `report_hash`。如果远端已经接收，但进程在本地 finalization 前退出，重启后继续使用原 body 完成本地状态，不重新调用模型生成另一份报告。

V2 路径先把报告保存为 `drv_v2_reports.status=pending`，远端成功后用 idempotency key 和 report hash 标为 `submitted`。重复 mark submitted 是幂等操作。

### 9.10 定时任务并发和跨日重试

`drv_scheduled_runs` 保存 run ID、报告日期、计划时间、时区、状态、尝试次数、错误和成功时间。

我实现的并发控制流程是：

1. 事务内 claim scheduled run；
2. running 且 lease 未过期时，其他进程不能接管；
3. lease 过期后允许新进程接管并增加 `attempt_count`；
4. `attempt_count` 同时作为 fencing token；
5. heartbeat 和状态更新必须携带当前 token；
6. 旧进程失去 lease 后无法再覆盖新进程结果；
7. `CollectDate` 固定使用原始 report date，午夜后重试也不会收集第二天的数据。

专项提交 `608013a fix(drv): fence scheduled delivery and gate migrations` 修改 6 个文件，新增 619 行、删除 137 行。

### 9.11 DRV 关闭时不能拖垮 LeapAgent

原问题是完整 `Store.Init()` 会一起执行 DRV V2 私有 schema migration。即使 `drv.enabled=false`，坏掉的 DRV 私有 schema 也可能导致整个 LeapAgent 启动失败。

我保留原 `Store.Init()` 的完整语义，同时新增：

```text
Store.InitCore(ctx)
Store.EnsureDRVSchema(ctx)
```

现在的顺序是：

```text
LeapAgent 核心启动 -> InitCore
DRV 未启用 -> 不要求 V2 私有 schema 健康
DRV 启用或热启用 -> EnsureDRVSchema -> 严格校验 -> 创建 DRV runtime
```

这项修改保证 DRV 关闭时不影响核心服务，同时没有放松 DRV 启用后的 schema 校验。专项提交 `ce6217b` 修改 7 个文件，新增 172 行、删除 7 行。

### 9.12 DRV 提交时间线

| 日期 | 提交 | 具体内容 |
| --- | --- | --- |
| 2026-08-06 | `2e79f68` | 增加确定性 V2 主链、市场数据、Assessment、计算、SQLite 和测试 |
| 2026-08-06 | `272b78d` | 补 durable delivery、调度、catch-up 和剩余风险处理 |
| 2026-08-10 | `04d3f92` | 收紧 Assessment、可信枚举、零交付和 pending review 边界 |
| 2026-08-10 | `ce6217b` | 将 DRV 私有 schema 与核心启动解耦 |
| 2026-08-10 | `95d4f60` | 增加完整 DRV V2 实现文档 |
| 2026-08-10 | `d203aba` | 修复审核隐私和 legacy delivery 兼容问题 |
| 2026-08-11 | `608013a` | 增加 scheduled delivery fencing 和 migration gate |
| 2026-08-11 | `64e1ffe` | DRV V2 确定性计算与运行时支持合入主线 |

以上提交作者均为 `taoduo`。中间的 merge commit 只是同步主线，不应重复计算为独立功能量。

## 十、爬虫逐站实现明细

### 10.1 一品威客 EPWK

入口和数据源：

```text
列表第一页：https://task.epwk.com/?o=7
后续分页：https://task.epwk.com/page{page}.html?o=7
详情页：https://task.epwk.com/{task_id}/
Sitemap：sitemap/1.xml 到 sitemap/12.xml
```

具体实现：

- `EpwkAdapter` 解析列表 SSR HTML 和详情页；
- 使用 `curl_cffi` Chrome impersonation 请求；
- 只加载 `epwk.com` 域内且未过期的 Cookie；
- 单域下载间隔约 0.5 秒；
- 解析 Nuxt `taskData`，提取任务 ID、标题、正文、交易模式、预算、雇主、分类、截止时间和中标信息；
- 列表价格、详情预算和正文提及价格分开保留；
- 详情 HTML 无法获得发布时间时才使用有界 Playwright 回退；
- 12 个 Sitemap 只视为有界 inventory，不冒充全站全集；
- 每条 Sitemap 候选仍请求详情核对标题、价格和发布日期；
- `lastmod` 与详情日期不一致时记录失败，不用 `lastmod` 回填；
- 403 后停止代表性探针，不无限切换身份重试；
- 每 50 条写一次 checkpoint。

实际解决的问题：旧数据虽然有 2,691 条任务，但报告仍为 partial，86 条详情未解决。后续真实抽查 150 条发现发布时间 0/150、投标数 1/150、地区 2/150，说明测试夹具中的字段不等于线上可稳定获得字段。因此我保留未知值，没有用推荐任务字段或 Sitemap 日期补造。

### 10.2 猪八戒 ZBJ

具体实现：

- `ZbjAdapter` 解析 SSR HTML 和 Nuxt 嵌入状态；
- 区分“需求预算”和“服务套餐价格”，不混成同一种价格；
- 保存固定价、区间价以及一个或多个套餐档位；
- 需求源使用实际发布时间排序路径 `r4/r4p{page}`；
- 按需求分类继续翻页，直到截止日期或自然末页；
- 跳转到 `verify.zbj.com` 或收到 429 时立即停止；
- 遵守 `Retry-After`，不自动绕过滑块和验证页；
- 交付中保留 1,668 条明确预算任务，并单独说明过滤的 5,414 条“待服务商报价”。

### 10.3 实现网 Shixian

具体实现：

- `ShixianAdapter` 分别扫描全职 `daily_wage_mode` 和兼职 `project_mode`；
- 保存平台预估报价、预估金额、一般月薪和雇主正文价格；
- 平台金额与正文薪资冲突时全部保留，不自行选择一个“正确值”；
- 处理“3个月前”“N年多前”“接近N年前”等模糊时间；
- 对“3个月前”只记录站点边界假设，不伪造精确日期；
- 扫描两个模式各自的自然末页。

正式交付 53 条，其中 34 条严格位于三个月内，19 条是页面只显示“3个月前”的边界记录。

### 10.4 程序员客栈 Proginn

`ProginnAdapter` 已实现 Python/Java 等人才分类和服务报价解析。这里的数据更接近人才日薪/月度服务报价，不是具体外包项目预算，所以在统一模型中保留站点和价格证据，但不与任务预算直接等价。

当前没有对应的正式大规模结果目录，因此本项可以描述为“实现站点采集策略和统一适配”，不能附加一个不存在的交付数量。

### 10.5 BOSS 直聘

采集难点是登录、网络风控、动态岗位接口和 851 个三级职业代码。

我做的具体工作包括：

- 评估第三方实现，保留 MIT 许可且本机实测可用的 Chrome/CDP 基础；
- 改造成按官方三级职业代码运行，而不是只跑单个关键词；
- 首次使用隔离 Chrome 完成人工登录，后续复用固定 profile；
- 通过真实搜索页导航监听页面内 XHR；
- 解析岗位标题、薪资、公司、分类、详情链接和分页状态；
- 每类默认间隔 12-20 秒；
- 对 `code:31/32/37`、HTTP 403/429 立即停止并原子记录冷却；
- 优先遵守 `Retry-After`，否则首次冷却 1 小时，重复限制最多退避到 4 小时；
- 每完成一个职业，先原子写数据，再推进 checkpoint 和 coverage；
- 参数不同的运行拒绝复用旧断点，防止混入不同数据口径；
- 数字薪资模式过滤面议、空薪资和 0 元，但不把它们算进 50 条配额；
- 最多翻 10 页补足一个职业的有效配额；
- 分开记录 `quota_met`、`exhausted_below_quota`、`page_limit_below_quota`、`empty` 和 `failed`。

最终完整元数据 40,750 条，有 JD 的清洗结果 39,594 条。

### 10.6 前程无忧 51job

站点实测职业目录为 26 个一级分类、130 条二级路径、1,246 条三级路径，对应 1,049 个唯一三级查询代码。

具体实现：

- 从当前页面读取官方三级职能树；
- 在 Playwright 页面环境中调用网站自己的签名请求；
- 相同三级查询代码只请求一次，再把岗位关联回全部官方分类路径；
- 直接使用搜索响应中的 `jobDescribe`，避免逐条打开详情页；
- 使用 SQLite 保存分类、分页、去重和完成状态；
- 以 `record_id` 跨页去重；
- WAF HTML、登录墙、限流和 schema 变化显式失败；
- 数字薪资模式最多有界检查 20 页，并在每页之间等待 3-5 秒；
- 区分职业覆盖率和每类配额达标率。

最终原始及清洗数据均为 27,552 条，其中 106 条源站没有明确薪资，保持空值，没有估算补造。

### 10.7 猎聘 Liepin

站点实测目录为 14 个一级类别、99 个二级类别、1,013 个三级职业页。

具体实现：

- 从官网 `/career/` 读取并校验三级职业目录；
- 目录少于 500 条时判断为页面异常或风控，不保存为完整目录；
- 使用 requests 解析职业页岗位卡；
- 列表阶段保存标题、薪资、公司、详情 URL 和官方分类；
- 将详情 JD 补采拆成独立阶段；
- 固定一个浏览器 Profile、单进程、单页面顺序访问；
- 用 `record_id` 建索引，已经成功的详情不重复访问；
- 遇到登录页或安全页保留 Cookie、保存断点并停止；
- 不再对浏览器 Context 调用 `clear_cookies()`；
- 详情合并时以原始岗位为基准，无 JD 的记录保持空值。

这里还处理过一个实际事故：旧工具连接了真实 Edge User Data 的 junction，在遇到安全页时执行 `context.clear_cookies()`，导致真实浏览器中多个网站的登录会话被清空。我定位了这一原因，并把策略改为保留 Cookie 后停止，同时明确禁止采集器直接使用日常 Edge Profile。

最终原始数据 49,056 条，补到有效 JD 46,607 条，2,449 条无 JD 记录只保留在完整元数据中。

## 十一、数据从抓取到最终交付的具体流程

### 11.1 外包数据流程

```text
站点调研
-> 确认任务/服务/人才报价的业务含义
-> 小样本验证标题、分类、价格和详情关联
-> 列表或 Sitemap 建候选池
-> 详情补采
-> 保留每个价格来源的 raw_text
-> 发布时间和窗口过滤
-> record_id 去重
-> JSONL/CSV/XLSX 导出
-> 分类统计和边界说明
```

价格数值化规则包括：

- 精确金额直接使用；
- 区间取上下限中点；
- “N 元以内”取上限；
- “N 万元以上”取下限；
- 面议、待报价和 0 元不进入明确数字价格样本；
- 原始文本始终保留，数值化结果不能覆盖原始证据。

### 11.2 招聘数据流程

```text
官方职业目录
-> 每个三级职业的有界岗位采集
-> 分类/分页/冷却 checkpoint
-> 原始岗位 JSONL
-> 对缺失 JD 的站点做详情补采
-> 按 record_id 合并详情
-> 清理 HTML、控制字符和空白
-> 筛出有 JD 的清洗数据
-> 写临时目录
-> 重新读取 JSONL/CSV/XLSX 核对
-> 原子替换正式结果
```

最终校验包括：

- 三种格式行数一致；
- `record_id` 无重复；
- 标题和三级分类缺失数；
- 薪资缺失数；
- JD 有效记录数；
- 完整元数据保留数量；
- XLSX 单元格截断数量；
- 以 `=` 开头的正文作为文本写入，避免 Excel 公式注入。

### 11.3 EPWK 质量分层流程

```text
2,121 条候选
-> 2,050 条详情成功
-> 筛出 1,997 条招标/雇佣主样本
-> 80 批 ChatGPT 逐条语义审核
-> A/B/C/D 和价格可信度
-> 1,200 条 A+B 主数据
-> 956 条高中价格可信度
-> 暂排 36 条疑似重复
-> 920 条保守建模集
```

每条审核记录保留：

- grade；
- 是否进入高质量集；
- price confidence；
- tags；
- reason；
- 原始任务字段和 record ID。

51 条 `duplicate_candidate` 只是疑似重复，没有直接物理删除；其中 36 条位于 956 条价格建模优先数据内，因此保守版本为 920 条。

## 十二、Facebook 模块和真实演进明细

### 12.1 主要模块分别做什么

| 文件 | 具体职责 |
| --- | --- |
| `start_facebook_stable.ps1` | 启动/恢复 AVD、ADB、代理和端口反向映射 |
| `facebook_transport.ps1` | 网络链路、代理和 transport 恢复 |
| `run_facebook_capture.ps1` | 单次抓取编排、UI 操作、抓包和 analyzer 调用 |
| `facebook_capture_addon_safe.py` | mitmproxy addon，捕获并脱敏保存目标 GraphQL |
| `analyze_facebook_capture.py` | 解析 Reaction/Share、用户、分页和 source proof |
| `facebook_capture_progress.py` | 长任务实时进度 |
| `facebook_capture_api.py` | 本地异步 Job API、状态、结果和取消 |
| `run_facebook_post_queue.ps1` | 多帖子队列和单 worker 串行调度 |
| `facebook_release_gate.py` | Probe、page2、3000+、A/B 和 Share 门控 |
| `facebook_batch_summary.py` | 批次结果汇总与失败分类 |
| `facebook_release_report.py` | 发布验收报告 |
| `validate_facebook_predeploy.py` | 部署前配置、依赖和边界验证 |

### 12.2 从 200 人上限到 3000+ 的过程

| 阶段 | 结果 | 当时证明了什么 |
| --- | --- | --- |
| 75 人测试 | 75 人、4 个 query、未 terminal | 小规模解析和 source proof 可用 |
| 200 人测试 | 200 人、9 个 query、absolute cap | 200 是程序上限，不是 Facebook 协议上限 |
| 1,350 人测试 | 约 54 页、未 terminal | 已突破 1,000，但仍不能称全量 |
| 40 次稳定性测试 | 375 人、约 2.98 秒/attempt | 连续分页没有 401/403/429 和 ANR |
| 180 次深分页 | 1,650 人、source exact | 不存在 1,000 人硬上限 |
| terminal run A | 3,180/页面约 3,182、12.9 分钟 | 首次明确 3,000+ terminal |
| terminal run B | 3,187/页面约 3,189、12.4 分钟 | 重复验证 source exact 和 terminal |
| API 全链路 | 3,209 人、31 分 40 秒 | API 到 Android、抓包、分析和结果返回全部跑通 |

这里最关键的工作不是单纯“向下滑更多次”，而是修正停止条件。历史 1,350 人结果虽然规模较大，但 `has_next_page=true`，因此被重新定义为 `partial-open-tail`，不再误报为全量。

### 12.3 terminal 如何从协议上证明

一轮 3,187 人的 terminal 抓包中共有 129 个 Reaction 页面记录：

- 128 个 `has_next_page=true`；
- 最后 1 个 `has_next_page=false`；
- 129/129 都有 `end_cursor`；
- `__ALL__` 前 127 页每页 25 人；
- 最后一页 12 人；
- 总计 3,187 人。

因此 terminal 的证据链是：

```text
目标 source_post_id 一致
AND cursor 分页连续
AND 最后一页 has_next_page=false
AND analyzer 没有跨 source 污染
```

到达脚本次数上限、超时、手工停止、UI 不再增长但 GraphQL 仍有下一页，都不能标记 terminal。

### 12.4 API 任务的状态和取消语义

API 暴露：

```text
POST /v1/jobs
GET  /v1/jobs/{id}
GET  /v1/jobs/{id}/result
POST /v1/jobs/{id}/cancel
GET  /health
```

取消逻辑的具体行为：

- 排队任务可以直接取消；
- 运行任务取消时终止本次 capture 子进程树；
- `cancelled` 写入 job manifest；
- API 重启后仍保留取消状态；
- 对 completed、failed、cancelled 再次 cancel 返回明确 no-op；
- 不删除已完成结果；
- 同一时间只允许一个 Android worker 使用模拟器。

### 12.5 Release Gate 为什么存在

Facebook 会话受限时，直接开始 3,000+ 长任务会浪费时间并增加账号压力。我实现了由低成本到高成本的门控：

```text
fresh Reaction Probe
-> 保守 page2
-> ADB 3000+
-> 再 Probe
-> Native 3000+
-> A/B 对照
-> 再 Probe
-> Share 验证
```

Probe 模式关闭 page2、分类遍历、深滚动和 fallback，只验证首页是否能正确打开目标帖、捕获目标响应并得到初始用户。遇到 session restriction、rate limit、source proof 失败或 ANR 时，门控停止后续高压力步骤。

### 12.6 Facebook 工作中解决过的典型问题

| 问题 | 排查结论 | 实际处理 |
| --- | --- | --- |
| App GraphQL 超时 | 原生 SecureTCP 没有稳定走普通 HTTP 代理 | 启用 Clash TUN/strict route，建立 adb reverse 和 mitmproxy 上游链 |
| 只能抓约 200 人 | 程序 absolute cap，不是平台上限 | 去除生产固定 cap，按 terminal 结束 |
| 大结果被误报全量 | `has_next_page=true` 仍被当成结束 | 引入 terminal/partial/open-tail 明确语义 |
| 抓到错误帖子响应 | feed 中存在其他帖子的 GraphQL | 强制 canonical URL、post ID 和 source_post_id 三层校验 |
| 长任务看不到进展 | 分析只在结束后运行 | 增加轻量 live progress 状态 |
| 多任务抢模拟器 | API 请求直接并发启动 worker | 改成异步 Job 队列和单 Android worker |
| Android ANR 遮挡目标帖 | UI 自动化继续执行但实际没有打开帖子 | 检测 `android:id/aerr_close`，先关闭 ANR 再严格重试 |
| 页面 2 被限制 | Facebook 返回 `1675004` 等会话限制 | Probe 和 release gate 熔断，不无限重试或轮换账号 |

## 十三、测试与验证到底覆盖了什么

### 13.1 DRV

现场 `go test ./internal/drv` 为 251 个测试通过。历史还执行过：

- focused Assessment 测试；
- `internal/drv` 整包；
- Windows race；
- `go vet ./internal/drv`；
- Linux/ext4 race；
- Linux/ext4 全仓测试；
- 真实 SQLite ModeShadow 隔离回放。

重点测试覆盖可信枚举、direct quote、pending review、跨日进度、shadow 隔离、catch-up、不重新评估重发、durable delivery、scheduled run fencing、schema 生命周期和热启用失败。

### 13.2 外包与招聘爬虫

历史定向验证记录包括：

- EPWK/共享测试 103 passed；
- BOSS 定向回归 19 passed；
- Python compile 检查通过；
- 150 条 EPWK 真实页面重采样；
- 输出 JSONL/CSV/XLSX 回读和行数核对。

2026-09-01 现场全 `tests` 目录运行结果为 336 passed、1 failed。失败位于猎聘 Edge CDP supervisor 测试，涉及新增的 `owner/restart_delay` 参数和本地 profile 指针夹具。这个结果不能写成当前全量测试全绿。

### 13.3 Facebook

历史验证包含 75、200、1,350、1,650、3,180、3,187 和 API 3,209 用户的不同阶段真实 run。

2026-09-01 现场 unittest 共运行 197 个，其中 4 个 release-gate 相关失败。历史真实结果仍是有效证据，但当前代码需要重新同步 release-gate 测试预期后，才能声称最新测试全绿。

## 十四、我的工作不只是“写爬虫脚本”

从实际工作内容看，可以拆成以下类型：

| 工作类型 | 具体例子 |
| --- | --- |
| 需求整理 | 区分任务预算、服务套餐、人才报价、月薪和正文薪资 |
| 数据源调研 | 检查 SSR、Nuxt、页面 XHR、官方职业目录、GraphQL cursor |
| 后端开发 | Go DRV 主链、SQLite 状态、Python 站点适配器和本地 API |
| 模型调用 | DRV Assessment 结构化评估、EPWK 1,997 条分批语义审核 |
| 数据处理 | 去重、详情合并、价格数值化、JD 清洗和 A/B/C/D 分层 |
| 测试 | 单元测试、失败路径、race、vet、格式回读和真实样本验证 |
| 故障排查 | WAF、登录墙、Cookie 清空、模拟器网络、ANR、分页停止条件 |
| 交付整理 | CSV/JSONL/XLSX、统计报告、边界说明、研发交接文档 |

因此更准确的岗位描述不是“只做了爬虫”，而是：完成了一套从业务口径、采集系统、模型评估、数据治理到后端计价和交付验证的完整数据工程链路。
