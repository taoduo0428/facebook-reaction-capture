# v1.0.1 — 合并 main、跨平台修复与仓库收敛

在 v1.0.0（后端现代化）基础上，把 main 的历史合并进来，并修掉 CI 在 Windows/macOS 上发现的三个真实缺陷。

---

## 修复

| 平台 | 问题 | 修法 |
|---|---|---|
| Python 3.12 | `mitmproxy 12.2.3` 把 `typing-extensions` 限在 `<=4.14`（`python_version < 3.13`），而 `pydantic 2.14.4` 要 `>=4.16` → `pip` 直接 `ResolutionImpossible` | 固定 `pydantic==2.11.9`（只需 `>=4.12.2`），3.12 / 3.13 均可解析 |
| Windows | manifest 导入报告里的 `→` 与破折号在 cp1252 控制台抛 `UnicodeEncodeError`，成功的 dry-run 变成失败 | stdout/stderr 重配 UTF-8（`errors=replace`）+ ASCII 兜底，并加测试 |
| macOS | `perf/sampler.py` 只读 `/proc`，macOS 没有 procfs → 采样恒为 `None` | 增加 `ps -o rss=,cputime=` 回退，含 `SS.ss` / `MM:SS.ss` / `HH:MM:SS` 解析与测试 |

顺带修掉一条既有时间敏感用例：导航预算断言不再假设「探针之后一定还有第二次等待」——机器繁忙时探针本身可能吃掉整个预算，那是对的。

## 仓库收敛

`main` 曾跟踪 **6,350 个文件**，其中大部分是本机状态。现移出跟踪（`git rm --cached`，**磁盘文件一个没删**，历史也不受影响）：

`runs/`(2,996)、`api-jobs/`(743)、`archive/`(662)、`transactions/`(539)、`evidence/`(130)、`.waylog/`(121)、`backups/`(111)、`.pyc`(80)、`api-batches/`(69)、`batches/`(34)、`dist/`(27)、`__pycache__/`(26)、`worker-bootstrap/`(19)、`platform-tools/`(13)、`jre/`+`lib/`+`patched-libs/`(162)、`.coding-tools/`+`.serena/`(14)、构建出的 APK(15)，以及根目录的日志、探针输出、`facebook.local.json`、`facebook_workers.json`、`jadx-*.exe`、`.lock` 等约 60 个。

**跟踪面 6,350 → 383 个文件**（源码 / 测试 / 文档 / 配置模板 / 发布清单）。依据见 `docs/backend-modernization/REPO_CLASSIFICATION.md`——项目自己的 `tools/maintenance_gate.py` 早已把这些目录列为非源码。

## 验证

- 本地全量：**1158 通过 / 5 跳过**
- CI（Windows + macOS）：**success**，且跑在**不含那些本机文件的干净克隆**上，证明仓库自洽
- 发布清单 `release-files.txt` 371 条全部可解析

## 边界（与前版一致）

- 调度器接管**派发决策**；执行与结果落库仍在 `JobManager` 既有路径（串行单设备下行为一致）。
- 真实设备验收（Windows/macOS 执行路径）需模拟器与登录态，未在本机执行；执行路径本身未改。
- Docker 仅用于后端，镜像未在本机构建验证（已用静态检查兜住约束）。
