# 2026-09-30 小范围无用代码清理

仅删除 6 个未使用导入、3 个当前内部未引用函数；净减少 62 行。没有改动捕获、来源匹配、分页、取消、租约业务规则，没有删除任何文件夹或历史数据。

完整本地回归：654 tests，2 skipped，0 failures，0 errors。修改后的 Python AST 与 PowerShell parser 通过，git diff --check 通过，发布清单 134 项可读取。

未运行新的真实抓取；检查时生产 API 未启动，未擅自启动服务。

回滚：逐文件先核对 change-manifest.json 中 after_sha256；匹配后从备份目录复制对应 before 文件。若文件又被修改，应按本次差异逐段回退，不能直接覆盖后续工作。

备份：C:\Users\lenovo\Desktop\facebook\backups\unused-small-code-20260930-164325
