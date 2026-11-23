# Git 仓库历史审计

审计基点为公开仓库 `main@8269cf3a3aeda7c6999ca2de3932973ec10a0bc3`。本次仅读取 Git 树、对象名称、类型和大小。统计命令在独立开发工作树执行。抓包和日志的内容以及其中是否存在仍然有效的凭据，均未核验。

## 体积来自哪里

| 口径 | 实测值 | 含义 |
|---|---:|---|
| 基点当前树 | 384 个文件，9,106,870 字节 | `git ls-tree -rlz 8269cf3` 的文件内容大小之和 |
| 基点可达历史 | 12,349 个对象，9,057 个唯一 blob，blob 原始大小合计 2,537,675,160 字节 | 同一内容的多个版本按各自对象计，多个路径共用同一 blob 只计一次 |
| 本机所有引用 | 12,383 个可达对象，9,071 个 blob，逻辑大小 2,537,826,325 字节 | 本轮开发已有提交，也含本地分支。引用继续增加时数值会变 |
| 本机 pack | 3 个 `.pack` 文件，1,412,408,264 字节 | 共享 Git 对象库。pack 内采用压缩和增量编码 |
| `git count-objects -v` | `in-pack=11957`、`size-pack=1379635` KiB | 含 pack 辅助文件的 Git 本机占用口径 |
| GitHub REST `repos/...` 的 `size` | 1,262,495 KiB（约 1.20 GiB） | GitHub 在查询时返回的仓库级统计 |

其中历史运行目录中的最大 `.mitm` 文件有 79,225,321 字节，另一份为 68,499,807 字节。工具链目录有 78,266,806 字节的 `.jar`。运行目录的单个 `.jsonl` 达 64,279,866 字节。`jre/` 内最大的模块文件为 53,657,809 字节。

| 后缀 | 基点历史唯一 blob 个数 | 原始大小合计（字节） |
|---|---:|---:|
| `.jsonl` | 178 | 1,387,175,718 |
| `.mitm` | 48 | 630,104,838 |
| `.jar` | 3 | 78,380,743 |
| `.zip` | 50 | 31,930,383 |
| `.pcap` | 2 | 2,214,906 |
| `.apk` | 7 | 75,078 |

分类按 `git rev-list --objects` 对每个唯一对象给出的一个路径。路径跨目录复用时，上表按该代表路径归类。压缩存储量受 delta 和 pack 布局影响，不能由原始字节逐项相加推断。

## 可复核的步骤

```text
git ls-tree -rlz 8269cf3
git rev-list --objects 8269cf3
git rev-list --objects --all
git cat-file --batch-check="%(objectname) %(objecttype) %(objectsize) %(objectsize:disk)"
git count-objects -v
gh api repos/taoduo0428/facebook-reaction-capture --jq .size
```

将 `rev-list` 每行的对象名送入 `cat-file --batch-check`，按 `objecttype=blob` 过滤后求和，再用同一行的代表路径分类。这些读取都不要求检出历史文件。

## 处置：2026-11-23 历史重写（已执行）

体积问题之外，只读扫描发现了更严重的事实：历史中的流量归档带凭据。

| 扫描对象 | 规模 | 结果 |
|---|---|---|
| `.mitm` 流量归档 | 48 个 / 600.9 MiB | **45 个含 `Authorization: OAuth <token>`**（token 长度 51–221 字符），`Cookie` 头为 0 条 |
| `.jsonl` 安全抓包 | 178 个 / 1.39 GiB | 未见真实凭据；`c_user=`/`xs=` 命中均为测试夹具与文档字段名 |
| Android UI XML | 大量 | `password` 命中是登录页输入框标签，不是值 |
| `facebook.local.json`（历史唯一版本） | 小 | 无凭据：`upstream_url` 不含 userinfo |
| 证书/私钥路径 | 4 条 | 仅 `.venv` 内公共 CA bundle 与 tornado 测试密钥，非本项目私钥 |

因此按"先轮换、再清理"的顺序处理。**清理不能替代轮换**：已 clone 的副本无法收回，Facebook 会话应在账号安全设置里登出全部会话。

### 已执行的操作

1. 完整镜像备份：`facebook-history-backup-20261123-152803.git`（12 个 ref、1,380,432 KiB pack），保留在仓库目录之外，未上传。
2. 在备份的全新克隆上用 `git filter-repo 2.47.0` 移除历史路径：`runs/`、`api-jobs/`、`api-batches/`、`archive/`、`transactions/`、`evidence/`、`backups/`、`batches/`、`dist/`、`worker-bootstrap/`、`platform-tools/`、`jre/`、`lib/`、`patched-libs/`、`.venv/`、`.coding-tools/`、`.serena/`、`.waylog/`，以及 `*.log`、`**/__pycache__/**`、`*.pyc`。
3. 校验：`main` 的树哈希重写前后完全一致（`35d55a0…`），即**当前代码内容零变化**。
4. 强制推送所有分支与标签，四个标签均落在重写后的历史上。
5. 同步本地两个工作树；`runs/`、`api-jobs/`、`api-batches/`、`backups/`、`platform-tools/`、`jre/` 等本机运行数据未受影响。

### 重写前后对比

| 指标 | 重写前 | 重写后 |
|---|---:|---:|
| 可达对象 | 12,760 | **1,558** |
| 本地 pack | 1,380,432 KiB（≈1.32 GiB） | **5,997 KiB（≈5.9 MiB）** |
| `main` 提交数 | 171 | 113（58 个只含被移除路径的空提交被剪除） |
| `.mitm` / `runs/` / `.venv/` 对象 | 48 / 数千 / 数千 | **0 / 0 / 0** |
| `main` 树哈希 | `35d55a0…` | `35d55a0…`（一致） |

### 提交映射（旧 → 新）

| 旧 | 新 |
|---|---|
| `f83bcb3`（v1.0.1 基线 main） | `8269cf3` |
| `b2b8270`（v1.0.1 标签目标） | `9bfc072` |
| `2a264a0`（v1.1.1 基线） | `745f934` |
| `01ef2a0` | `ec912ee` |
| `4238c1c`（Phase K 实测提交） | `29b66f3` |
| `32333bf`（重写前 main） | `5e33f13` |
| `003b2d2`（产品化提交） | `3855561` |

当前文档中描述**当前状态**的引用已按上表更新。早于 2026-11-23 的历史记录（`PHASE-*`、`STABILIZATION_*`、`VALIDATION_*`、`specs/001-*` 等）里出现的 SHA 属于重写前历史，无法在重写后的仓库中解析；需要时从上面的备份镜像取。

### 仍需人工处理的两件事

1. **GitHub 侧尚未真正回收**。重写只改了分支与标签；`refs/pull/*/head` 仍指向旧提交（例如 `refs/pull/12/head` → `003b2d2`），旧对象因此仍可达，仓库体积在 API 里也暂时仍显示约 1.21 GiB。需要向 GitHub Support 提交 "purge cached views / run gc on repository" 请求，并说明已 force-push 清除敏感数据。
2. **会话轮换**。`.mitm` 里的 `OAuth` token 属于当时登录的账号；即使可能已过期，也应按已泄露处理：登出全部会话，必要时改密码。

## 预防：不要再把运行数据带进历史

- 发布只走 `release-files.txt` 白名单（`tools/export_source.py` 会拒绝软链接、缺失文件与非法路径，并输出 SHA-256 清单）。
- `.gitignore` 已覆盖 `runs/`、`api-jobs/`、`api-batches/`、`backups/`、`platform-tools/`、`jre/`、`lib/`、`.venv/`、`dist/`；新增本机目录时同步加规则。
- 提交前用 `git status --short` 与 `git diff --cached --stat` 复核；出现 `.jsonl`、`.mitm`、`.pcap`、`.jar`、`.zip`、`.apk`、`.exe` 一律先停下。
- 需要保留抓包作为证据时，放在仓库外的目录，并在文档里只记录路径与摘要。
- 给克隆者的建议：`git clone --depth 1` 或 `git clone --filter=blob:none`，避免下载无关历史。
