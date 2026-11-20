# 2026-09-15 可恢复目录整理

已归档 50 个文件，10,892,579 字节（约 10.39 MiB），未删除数据。

- 根目录减少 35 个杂项：21 张旧截图、3 个旧探测结果、10 个验证日志/暂存路径记录、1 个旧工具包。
- `dist/` 的 15 个旧源码包移到此处的 `old-packages/`，最新 `facebook-source-20260915-165601.zip` 保留原位。
- 本次不改变源码、测试、配置、登录状态、证书、锁、运行日志、模拟器、依赖、API 任务和历史采集数据的位置。
- 有现有脚本引用的 `anonymous-public-probe.json`、`current-ui-probe.xml`、`predeploy-validation.json` 保留原位。

`plan.json` 记录原路径和归档位置；`receipt.json` 记录每个文件的大小与 SHA-256。归档时已核对 50 个文件完整性，并对发布清单的 95 个源码文件及两个本机配置文件执行前后哈希比对，97 个文件全部未变。固定文件名引用的排查是有界源码检索，历史产物仍完整保存在此处。

## 核对归档

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\archive\cleanup-20260915-folder-tidy\organize.ps1 -Mode Verify
```

## 恢复原目录

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\archive\cleanup-20260915-folder-tidy\organize.ps1 -Mode Restore
```

恢复不会覆盖后来生成的同名文件；发现冲突会停止并说明原路径。请保留归档目录和清单，直至不再需要恢复。本次整理减少目录杂项，不宣称释放磁盘空间。
