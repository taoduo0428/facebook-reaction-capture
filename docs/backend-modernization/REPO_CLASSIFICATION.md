# 仓库分类与 Git 跟踪策略

- 生成时间：2026-11-21
- 适用版本：v1.0.0（后端现代化完成后）
- 结论：**不删除任何文件**。跟踪范围收敛到 `release-files.txt`（发布清单），
  其余按类别忽略并保留在本机。

---

## 1. 分类结果

统计口径：工作区实际文件；"跟踪"列指 `git ls-files` 是否收录。

| 类别 | 典型路径 | 体量 | 跟踪 | 说明 |
|---|---|---|---|---|
| **Source Code** | `facebook_capture_api.py`、`facebook_*.py`、`facebook_backend/**`、`web/`、`web_v2/`、`macos/`、`tools/`、`perf/` | ≈6 MB | ✅ | 发布清单里的全部源码 |
| **Tests** | `tests/**`（41 个模块 + `manual/`） | ≈3.8 MB | ✅ | 已迁入 `tests/` 包，根目录不再有 `test_*.py` |
| **Documentation** | `README.md`、`API_USAGE.md`、`WINDOWS_DEPLOYMENT.md`、`docs/**`、`specs/**`、`CHANGELOG.md` | ≈2 MB | ✅ | 含本次 `docs/backend-modernization/**` |
| **Configuration templates** | `facebook.local.example.json`、`facebook_workers.example.json`、`requirements.txt`、`alembic.ini`、`.github/workflows/check.yml` | <100 KB | ✅ | 模板与 CI |
| **Runtime Data** | `runs/`（1.84 GB）、`api-jobs/`（1.8 MB）、`api-batches/`、`batches/`、`transactions/`、`share_users/`、`exports/`、`worker-bootstrap/`、`data/` | ≈1.9 GB | ❌ | 服务运行时状态，可由任务与数据库重建 |
| **Generated Artifacts** | `dist/`（15 MB）、`perf/results/`、`__pycache__/`、`*.pyc` | ≈16 MB | ❌ | 构建/压测输出 |
| **Historical Evidence** | `evidence/`、`archive/`、`backups/`、`docs/archive/`、根目录 `*.log` | ≈300 MB | ❌ | 历史证据与备份，保留在本机 |
| **Local Configuration** | `facebook.local.json`、`facebook_workers.json`、`*.pem`、`*.key`、`*.der`、`*.keystore` | <10 KB | ❌ | 本机配置与密钥，**不得入库** |
| **Build Cache / Toolchain** | `.venv/`（243 MB）、`jre/`、`lib/`、`patched-libs/`、`platform-tools/`、`.coding-tools/`、`.serena/`、`.waylog/` | ≈450 MB | ❌ | 平台相关、体积大、可重建 |
| **Large reference** | `SSL-Pinning-Bypass-ADB.pdf/.txt` | 3.4 MB | ❌ | 参考资料，不在发布清单 |

`.git/` 本身 1.3 GB（历史对象），不属于工作区文件。

## 2. 为什么用"发布清单"而不是"全量镜像"

改造前仓库的 `.gitignore` 一度改成"镜像工作区、只排除两个超大文件"。那样做的问题是：
`runs/` 1.84 GB、`.venv/` 243 MB、`backups/` 110 MB 会被一并跟踪，仓库不可用。

现在的规则（`.gitignore` 顶部也写了）：

1. **跟踪**：`release-files.txt` 里的每一条（`tools/export_source.py` 用它生成发布包，
   `tests/test_layout.py` 校验它包含全部测试）。
2. **忽略**：上表里标 ❌ 的类别。
3. **不删除**：忽略只是不跟踪，文件仍在磁盘上。

## 3. 本轮实际修复的跟踪缺陷

改造前的仓库**无法从克隆复现**：

| 缺陷 | 后果 | 处理 |
|---|---|---|
| `facebook_http_security.py`、`facebook_share_contract.py` 未跟踪 | `facebook_capture_api.py` 直接 import 它们，克隆后服务**起不来** | 已加入跟踪 |
| `tests/` 只有 9 个文件被跟踪，另外 40 个（含 `tests/__init__.py`）未跟踪 | CI 的 `tests.test_facebook_capture_api` 在克隆里不存在 | 已补全 |
| 31 个根目录 `test_*.py` 仍被跟踪，但工作区已删除 | 跟踪树与磁盘不一致 | 记为 30 个重命名 + 1 个删除 |
| 26 个文件有未提交改动（README、AGENTS.md、脚本、web_v2） | 克隆内容与所有基线测试跑的内容不一致 | 以工作区版本提交（那是权威版本） |

对应提交：`e049d4d chore(repo): track the published tree and finish the tests/ relocation`。

## 4. 本轮实际执行（跟踪面收敛）

`main` 上曾跟踪 **6,350 个文件**，其中大部分是本机状态。已按上表把它们**移出跟踪**（`git rm --cached`，磁盘文件一个没删）：

| 移出跟踪 | 文件数 |
|---|---|
| `runs/`（任务运行产物） | 2,996 |
| `api-jobs/`（job manifest 运行态） | 743 |
| `archive/`（历史归档，含 pcap/mitm） | 662 |
| `transactions/` | 539 |
| `evidence/` | 130 |
| `.waylog/` | 121 |
| `backups/`（测试副本） | 111 |
| `.pyc` | 80 |
| `api-batches/` | 69 |
| `batches/` | 34 |
| `dist/`（构建产物） | 27 |
| `__pycache__/` | 26 |
| `worker-bootstrap/`（运行时模板与缓存） | 19 |
| `android-ui-dump-helper/build/`（编译出的 APK） | 15 |
| `platform-tools/`（adb 等二进制） | 13 |
| `.coding-tools/`、`.serena/` | 14 |
| `jre/`、`patched-libs/`、`lib/` | 162 |
| 根目录散文件（日志、探针输出、`facebook.local.json`、`facebook_workers.json`、`jadx-*.exe`、APK、`.lock`） | 约 60 |
| **合计** | **约 5,970** |

跟踪面：**6,350 → 383 个文件**（源码、测试、文档、配置模板与发布清单）。

同时 `.gitignore` 补齐了新出现的类别（`/fresh-export/`、`*.lock`、探针 JSON/XML、`jadx-*.exe`、`android-ui-dump-helper/*.apk`、`specs/**/*.tmp`），`git status` 在移出后为 **0 条未跟踪残留**。

验证：移出跟踪后全量 `1158 通过 / 5 跳过`（磁盘文件仍在，功能无变化）；`release-files.txt` 371 条仍全部可解析。

**历史不受影响**：这些文件仍在 git 历史里，克隆后可见；本操作只改变"当前树跟踪什么"。

## 5. 如何核对

```powershell
# 跟踪清单与发布清单是否一致（应无输出）
$manifest = Get-Content release-files.txt | Where-Object { $_ -and -not $_.StartsWith('#') }
$tracked = git ls-files
$manifest | Where-Object { $_ -notin $tracked }

# 发布清单里的文件是否都存在（tools/export_source.py 会逐个校验）
.venv\Scripts\python.exe tools\export_source.py

# 确认关键源码没有被忽略
git check-ignore -v facebook_capture_api.py facebook_http_security.py tests\test_facebook_capture_api.py
```

## 6. 新增文件时的规则

1. 新源码/测试/文档 → 同时加进 `release-files.txt`（`tests/test_layout.py` 会检查
   `tests/test_*.py` 是否都在清单里）。
2. 运行时产物、日志、数据库、抓包 → 确认 `.gitignore` 覆盖，不要 `git add -f`。
3. 新增顶层目录时，先判断它属于上表哪一类，再决定是否加忽略规则。
