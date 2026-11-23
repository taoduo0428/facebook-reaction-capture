# v1.1.1 — 升级启动兼容修复

v1.1.0 把「旧清单 + 未迁移」的安装判定为需要人工迁移，这是预期行为；但扫描本身过严：它把 `api-jobs/` 下任何以 `.job.json` 结尾的文件都当成旧清单，遇到现场常见的 `fb-linux-pending.job.json`、`fb-restored-error.job.json` 这类手写文件时直接抛错，升级后服务无法启动。

## 修复

历史加载器只 glob `fb_*.job.json` 与 `batch_*.batch.json`，其余文件从来不是运行时状态。现在启动扫描沿用同一规则：

| 文件 | v1.1.0 | v1.1.1 |
|---|---|---|
| `fb_<id>.job.json` / `batch_<id>.batch.json` | 计入状态与摘要 | 同左 |
| `fb-linux-pending.job.json`（前缀不匹配） | 抛错，拒绝启动 | 跳过，不删除、不进摘要 |
| `notes.txt` 等其他文件 | 忽略 | 忽略 |
| 前缀匹配但为符号链接/非普通文件 | 抛错 | 抛错（真实篡改仍然拒绝） |

## 行为不变

- 存在真实旧清单且未迁移时，`auto` / `database` 仍**拒绝启动**并提示 `legacy_manifest_import --apply` 与 `--verify`，不会静默切换。
- `FACEBOOK_JOB_STORE=manifest` 仍是一行回退。
- 非状态文件原样保留，不被删除、不被改写。

## 验证

- 本地全量：`Ran 1323 tests`，`OK (skipped=9)`。
- 新增回归：非状态文件与真实清单共存时摘要只随真实清单变化，`manifest` 模式可回退且文件字节不变。
- 分支 CI：Windows / macOS 契约检查与容器镜像构建均 success。

## 升级建议

已部署 v1.1.0 的安装直接升到 v1.1.1；`api-jobs/`、`api-batches/`、`runs/` 与账号配置无需改动。
