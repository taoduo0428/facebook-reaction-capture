# 发布流程

当前发布基线为 [v1.0.1](RELEASE_NOTES_v1.0.1.md)。标签 `v1.0.1` 指向 `b2b8270`，发行说明随后合入 `main@f83bcb3`。首个后端版本 v1.0.0 的 FastAPI 迁移与契约验证见[历史发行说明](RELEASE_NOTES_v1.0.0.md)。

## 1. 确定版本

HTTP 契约或状态语义发生不兼容变化时递增 MAJOR。向后兼容的新能力递增 MINOR。修复和文档更新递增 PATCH。准备下一次正式发布时，在同一份候选源码中更新以下项目：

- `facebook_backend/__init__.py` 的 `__version__`。FastAPI 从这里读取公开版本。
- README 的「后端 vX.Y.Z」标题和发行说明链接。
- CHANGELOG 顶部的正式版条目 `## vX.Y.Z — 日期`。
- `docs/backend-modernization/RELEASE_NOTES_vX.Y.Z.md` 的文件名和首行标题 `# vX.Y.Z — 主题`。将该文档加入 `release-files.txt`。

在下一次发布准备就绪前，开发计划单独记录。已发布标签和 GitHub Release 保留原样。

## 2. 核验候选源码

从项目根目录运行。普通 push、PR 或没有 `.git` 的源码包都可以检查包与文档的一致性：

```text
python tools/check_release_version.py
```

标签构建额外传入触发发布的**实际标签名**。GitHub Actions 的 tag push 在 Bash 和 PowerShell 中分别运行：

```bash
python tools/check_release_version.py --tag "$GITHUB_REF_NAME"
```

```powershell
python tools/check_release_version.py --tag "$env:GITHUB_REF_NAME"
```

版本不符时退出码为 1。标签必须来自发布事件或已检出的标签引用。从 `__version__` 拼出 `--tag` 会失去这项比较。普通分支构建省略 `--tag`，仍执行文档核验。

按[测试说明](TESTING.md)执行平台合约、依赖检查与静态检查，记录提交、平台、退出码和跳过数。用 `python tools/export_source.py` 按 `release-files.txt` 生成源码 ZIP 和 SHA-256 清单。核对本次 ZIP 中的版本、发行说明和摘要，避免误取历史 `dist/` 包。

## 3. 打标签与交付

候选提交经 PR 合并、检查通过后，在约定提交创建一个新的 `vX.Y.Z` 注释标签。标签构建通过第 2 节核验后，用同一标签、对应的发行说明和这一份已核对的 ZIP 创建 GitHub Release。

在全新目录解压该 ZIP，使用 Python 3.12 或 3.13 创建虚拟环境。安装 `requirements.txt` 后重新运行版本检查、`python -m pip check`、文档与 API 契约测试以及 `python -m facebook_backend --help`。数据库迁移检查只指向临时 SQLite 库。真实设备验收结果另按[测试说明 §5](TESTING.md#5-真实设备验收与自动化分开记录)记录。

## 4. 回退

服务侧回退前先停止接收任务，确认旧执行者与传输清理状态，保全数据库和 manifest。在独立目录恢复已核对的发布包及匹配的状态备份。切换数据来源见[状态迁移步骤](STATE-STORE-CUTOVER.md)。发布内容需要更正时创建新补丁版本，以便现有安装继续定位原始标签与产物。
