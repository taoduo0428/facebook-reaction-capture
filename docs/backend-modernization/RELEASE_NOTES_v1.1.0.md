# v1.1.0 — 生产化收口

在 v1.0.1 之上，本轮把「数据库只是可选项、调度器只是决策器」推进为「一个状态来源、一个调度主体、一套可核验的执行证据」。采集算法、五类 URL、API 参数与响应字节均未改变。

## 一、状态来源

| 项 | v1.0.1 | v1.1.0 |
|---|---|---|
| 新安装默认 | `manifest` | `auto`：全新默认库自动建库并迁移到当前 head |
| 已有安装 | 无强制 | 显式 `database` 要求 schema 已迁移并核对版本、表、主键、索引与完整性 |
| 旧清单存在 | 可静默回退 | 必须有对应的离线核对收据，摘要变化或行缺失即拒绝启动 |
| 批次受理 | 逐子任务分别提交 | 父行、子任务、位置关系、创建事件同一事务 |
| 重启成员关系 | 查磁盘 JSON 授权 | 只认数据库里已提交的批次关系 |
| 回退 | 导出覆盖 | 导出到全新目录，逐字段重建并核对结果 |

离线工具在停机状态持有状态所有者锁运行：`--dry-run` 只统计，`--apply` 单事务写入并在读回后落收据，`--verify` 只读比对，`--rollback --rollback-dir NEW_DIR` 产出独立回退包。孤儿、重复成员、顺序冲突、未决进程或租约、无法无损表达的字段都会阻止操作。

## 二、调度与执行

数据库模式下只有一个 `ProductionScheduler` 线程：`BEGIN IMMEDIATE` 内完成全局租约检查、SQL 选队、尝试与 `job.claimed` 事件，结算时复核 token/fence/attempt 再更新结果、诊断、尝试、事件与租约。内存对象只作为兼容读视图，唤醒信号不携带执行权。

保留的可靠性语义：`cancelling` 不等于 `cancelled`；请求取消成功不等于进程已停止；`stop-unverified` 阻止设备再次使用；进程身份未知阻止接管；传输清理未完成阻止复用；结果持久化失败保留诊断并要求显式对账；限流不换账号或设备续跑；超时不伪造停止。

启动恢复只处理「可证明从未启动」的尝试（`not-started` + `not-required`，且 `started` 事务会先使其失效）。其余情况——疑似副作用、PID 或清理不明、租约代次不符、存在竞争尝试、结果写失败——保留租约与诊断。

## 三、跨平台证据

- **进程身份**：macOS 用内核提供的亚秒级出生时间（`psutil`），Windows 保留 `GetProcessTimes` 的换算顺序；两平台都精确比对，`PermissionError` 与不可靠探测一律 `unknown`。
- **进程树**：Windows 在工作根 `CreateProcessW` 时原子加入匿名 Job Object，只有 `ActiveProcesses == 0` 才标记 `stopped`；查询失败保持未知，只终止自己持有的根。
- **传输恢复**：每次执行写入绑定 job/attempt/runner PID/序列号/端口/启动时间的凭据，Windows 额外读回代理与 reverse 映射，并在进程树清空后由宿主恢复 watchdog 并核对 PID/PPID/脚本/序列号/端口。

## 四、可观测性与产物

指标标签限基数（未匹配路径统一 `unmatched`、非标准方法统一 `OTHER`），W3C `traceparent` 从 HTTP 贯穿到数据库与执行线程，敏感字段（请求行、URL、异常正文、幂等键）不进入结构化日志、指标或导出的 span。旧标准库入口不再打印原始请求行。

新增 `artifact_integrity` 基线与流式 SHA-256：只在写入方声明完成后取样，先比 `size:mtime_ns:inode` 再决定是否重算，文件变化报告 `changed` 而不是自动「修复」。历史产物缺少可信基线时保留 `unknown`，不改变既有 completed 语义。

## 五、部署

容器镜像改为**无设备只读控制面**：读接口与 `/health`、`/metrics` 可用，提交、取消、对账返回 503 并说明需在原生宿主执行；`runs/`、`api-jobs/`、`api-batches/` 不再挂进容器。Windows/macOS 原生部署不变。

## 六、验证

- 本地全量：`Ran 1323 tests`，`OK (skipped=9)`。
- 分支 CI：Windows 与 macOS 通过；容器 workflow 真实构建镜像、等待 healthcheck、断言 503 后销毁。
- 性能（合成夹具、固定负载）：读 9.0 RPS / p95 11.6 ms，提交 3.0 RPS / p95 18.0 ms；SQLite 两线程写入 152 笔/秒，流式 SHA-256 256 MiB/s。详见 [v1.1 性能](V1.1-PERFORMANCE.md)。

## 七、边界

真实 Android 设备采集、真实 macOS 宿主执行与真实账号验收**未在本轮执行**，清单见[测试说明第 5 节](TESTING.md#5-真实设备验收与自动化分开记录)。本机没有 Docker，镜像构建由 CI 记录，不作为本机结论。仓库历史体积与清理建议见[仓库审计](GIT_REPOSITORY_AUDIT.md)。
