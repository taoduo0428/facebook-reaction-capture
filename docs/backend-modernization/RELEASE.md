# 发布流程（v1.0.0）

- 当前版本：**v1.0.0**（`facebook_backend/__init__.py::__version__`）
- 发布物：源码包（`tools/export_source.py` 生成）+ Git 标签 + GitHub Release
- 前提：`docs/backend-modernization/TESTING.md` 的必跑命令全绿

---

## 1. 版本策略

| 变化 | 版本位 |
|---|---|
| HTTP 契约、状态语义、产物名称变化 | MAJOR |
| 新增接口/模块/能力，旧行为不变 | MINOR |
| 修复、文档、内部重构 | PATCH |

本轮（v1.0.0）把 HTTP 层迁到 FastAPI、加了数据库/调度器/可观测性，**HTTP 契约与状态语义未变**
（有双服务器逐字节对比测试），因此作为首个正式后端版本发布。

## 2. 发布前检查清单

```powershell
$env:PYTHONPATH = "."
.venv\Scripts\python.exe -m unittest discover -s tests -t .   # 1111 OK / 4 skipped
.venv\Scripts\python.exe -m pip check
node --check web/app.js; node --check web_v2/app.js; node --check examples/capture_fetch.mjs; node --check perf/k6_api.mjs
.venv\Scripts\python.exe tools\export_source.py                 # 清单校验 + 生成 dist/ 源码包
.venv\Scripts\python.exe -m alembic heads                       # 0002_job_pinning (head)
git status --porcelain                                          # 只应有未跟踪的运行时目录
```

发布包内容 = `release-files.txt`（353 条），生成在 `dist/`（已被 `.gitignore` 忽略）。

## 3. 打标签与发布

```powershell
# 1) 确认版本号
Select-String -Path facebook_backend\__init__.py -Pattern "__version__"

# 2) 打标签（注释标签，写清范围）
git tag -a v1.0.0 -m "Facebook Automation Platform v1.0.0 — FastAPI backend, persistence, device-aware scheduler, observability"
git push origin v1.0.0
git push origin HEAD

# 3) 创建 GitHub Release（gh CLI）
gh release create v1.0.0 dist\facebook-source-*.zip `
  --title "v1.0.0 — 后端现代化" `
  --notes-file docs\backend-modernization\RELEASE_NOTES_v1.0.0.md
```

> 没有 `gh` 时：在 GitHub 网页端用同一个 notes 文件创建 Release，上传 `dist/` 里的 zip。

## 4. 分支与合并

- 当前工作分支：`codex/stabilization-20260915`（领先 `origin` 若干提交）。
- 发布前先把该分支推到远端并开 PR 合到 `main`；**不要**强推、不要改写已有历史。
- 合并后 `main` 上再打标签更规范；若直接在本分支打标签，Release 里注明来源分支。

## 5. 发布后验证（全新目录）

```powershell
# 解压 dist 里的源码包到全新目录，验证"克隆即可跑"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "."
.venv\Scripts\python.exe -m unittest discover -s tests -t .
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m facebook_backend --help
```

这一步是 v1.0.0 的硬门槛：改造前仓库缺少 `facebook_http_security.py` 等被 import 的文件，
全新目录根本起不来；现在必须能跑通。

## 6. 发布内容清单

| 类别 | 文件 |
|---|---|
| 架构 | `docs/backend-modernization/ARCHITECTURE.md` |
| 基线 | `BASELINE.md`（改造前事实快照） |
| API | `API_COMPATIBILITY.md`（逐接口新旧对比） |
| 数据库 | `DATABASE.md` |
| 调度 | `SCHEDULER.md`、`WORKER.md` |
| 部署 | `DEPLOYMENT.md`、`WINDOWS_DEPLOYMENT.md`、`macos/CODEX_DEPLOYMENT_RUNBOOK.md` |
| 测试 | `TESTING.md` |
| 排障 | `TROUBLESHOOTING.md` |
| 性能 | `PERF_REPORT.md` |
| 仓库 | `REPO_CLASSIFICATION.md` |
| 阶段报告 | `PHASE-01` … `PHASE-08` |
| 变更日志 | `CHANGELOG.md` |

## 7. 回滚发布

```powershell
git tag -d v1.0.0                     # 本地删标签
git push origin :refs/tags/v1.0.0     # 远端删标签
gh release delete v1.0.0 --yes
```

服务侧回滚：`start_facebook_capture_api.ps1 -Legacy` 或 `git checkout <上一个标签>`。
