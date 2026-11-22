# 2026-09-30 测试目录整理

## 变更

32 个根目录 Python 测试移动至 tests/；2 个独立 PowerShell 夹具移动至 tests/manual/。保留全部 654 个原有测试；新增 3 个目录/发布清单/CI 路径回归。修改测试相对根目录、测试间导入、Windows/macOS CI 与当前文档命令、verify_raw_capture_layer.ps1 测试入口、Git 允许项及发布清单。未搬业务源码、启动器、设备/账号配置或历史运行产物。

旧命令 python -m unittest test_xxx 改为 python -m unittest tests.test_xxx；根目录默认 discovery 仍可发现全部测试。新规范命令见 tests/README.md。

## 验证

- 原有 654 个测试 ID（去掉新增包名前缀）一个未丢失；测试断言与逻辑未改写，移入的 34 个文件只包含预期路径变更。
- 受影响聚焦套件：65 tests，1 skipped，0 failures/errors。
- 当前完整套件：657 tests，2 skipped，0 failures/errors。
- 139 文件源码 ZIP 完整性、精确文件集、逐文件字节校验通过；独立解压目录完整套件：657 tests，2 skipped，0 failures/errors。
- 两个 PS XML 夹具：移动前、移动后均通过。
- 发布清单无被 Git 忽略的项目；git diff --check 通过。
- 原来 6 个清理过的运行源码字节不变。没有重新启动服务、操作真实模拟器、修改账号、提交或推送 Git。Windows 本机验证不代表已运行托管 CI 或真实 Mac。

## 回滚

完整备份位于：C:\Users\lenovo\Desktop\facebook\backups\tests-layout-20260930-165528

change-manifest.json 保存原位置、新位置、修改前后哈希及元数据备份索引。回滚前先确认当前文件仍与记录的 after_sha256/metadata hash 相同，且旧位置没有被其他人新建。满足后逐文件把 tests/ 中对应文件移回原位置，并恢复其备份字节；再恢复列出的 CI/文档/清单元数据。新建的 __init__.py、test_layout.py、README.md 仅在哈希匹配且没有后续修改时移到本事务目录保留。不要递归删除 tests/ 或覆盖后续工作。

源码 ZIP 与 fresh-export 均为本机验收产物，留在事务目录，不加入发布清单。
