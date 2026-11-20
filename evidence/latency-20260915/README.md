# 连续采集耗时优化与实机验收

本轮保留用户已有修改；回滚备份为 `backups/latency-optimization-20260915/`。未更改 GraphQL/MITM 数据逻辑、账号、登录状态、代理配置、并发数或分页限流参数。

## 最终修改

- `facebook_transport.ps1`：`Confirm-FacebookUiOnline` 先原地读 UI。Facebook 前台、非离线/阻塞、有效内容才能直接返回；否则进入原有有界恢复。
- `run_facebook_capture.ps1`：常驻 hook 成功记录绑定 serial、boot_id、main PID、kernel starttime、JS SHA-256 和两条 hook-installed 日志。UI 就绪前后校验身份，失配/显式冷启动/证据缺失仍完整注入；替换注入前失效旧缓存，前后进程身份一致才能写新缓存。只读身份查询失败时放弃缓存。运行状态在 `worker-bootstrap/runtime-emulator-5554-unpin.json`，不属于发布源码。
- `explore_facebook_readonly.ps1`：Reaction 点击及严格详情展开后 250ms 做新快照。安全入口/列表已准备好时提前继续；未就绪按已用墙钟时间补足原等待预算。列表 loading 交由原有 readiness 处理；保留两次 source guard、群组限定分支及评论排除。
- 新增 `test_facebook_transport_health.py`、`test_facebook_navigation_latency.py`、`test_facebook_unpin_reuse.py`；同步 `.gitignore`、`release-files.txt`、`.github/workflows/check.yml`、`CHANGELOG.md`。

## 实机结果

最终批次：`batch_09f57094a0b94460`；通过当前 API 提交两条相同历史 URL、查询状态和下载 JSON 结果。

| 指标 | 第一帖旧值 | 第一帖本轮 | 第二帖旧值 | 第二帖本轮 |
| --- | ---: | ---: | ---: | ---: |
| Job 总秒数 | 131.809 | 112.094 | 173.860 | 169.185 |
| 开始到目标确认，秒 | 71.787 | 58.918 | 39.156 | 25.052 |
| 目标确认到列表，秒 | 21.897 | 21.437 | 21.227 | 15.124 |
| 分页秒数 | 17.118 | 19.039 | 90.940 | 116.393 |
| 用户数 | 175 | 185 | 815 | 913 |

旧批次为 `batch_ef3f1ceaf8fb41da`。第一帖 `share/p/1Jetj5opcE/` / source `1476320540987565`，第二帖 `share/p/1TWnMcMtmF/` / source `1637643921290478`。

本轮 job 为 `fb_21f376fa624b4743`、`fb_713112984e334578`。二者同一 Facebook 主 PID `1839`，结束后 PID 仍一致，日志均显示健康原地返回。第一帖首次注入 `elapsed_ms=14126`，第二帖 `reused eternalized hooks ... reuse_elapsed_ms=3313`；该 3313ms 包含 UI/身份检查，与纯注入时间口径不同。

两帖终页均确认、source mismatch=0、normalized_via_comments=false、rate_limits=0、temporary_feature_block=0。第一帖枚举可见用户185，UI reported186，完整性明确为 `complete-terminal-visible-subset`（差1，不能声称全部186身份已获取）；第二帖913/913，38响应页，完整性 `complete`。结果 JSON 保存在 `retained-optimizations/`，未隐藏该可见性差异。

这不是严格同负载基准：旧第一帖有一次目标重试，本轮第一帖有冷启动后桌面切回；第二帖数据从815增至913，分页从34页增至38页。因此只报告各段实测，不声称所有工作负载等比例提速。初始分页4秒cooldown、min cycle、loader backpressure、rate-limit backoff原值未变。短期未出现限制不能保证未来账号不受平台限制。

## 验证与撤回记录

- 最终实际函数测试：23项通过（warm 3、health 6、navigation 6、unpin 8）。导航改动后的既有 orchestrator 123项通过，总计146项；PowerShell语法与git diff检查通过。
- 被撤回的 MITM TCP readiness 优化曾通过模拟测试，但实机 `batch_f4aa8e7f92ea4316` 两任务在进入帖子前失败：进程层级识别误拒绝了本项目 listener。已将该代码块精确恢复到本轮前备份，原3秒等待/重试/端口保护不变。保留原失败报告和 `withdrawn-readiness-test.py`，后者不在CI或源码发布清单。
- `Open-Url` 后8秒等待本轮未缩短；不以旧页面可见或旧抓取结果替代本次导航的严格确认。
- 开始验收时发现API/模拟器已停止，使用现有START入口恢复；没有改配置。最终health实测API ready、worker idle、queue0、Android device，UI HTTP200；Facebook PID仍为1839。

最终源码包为 `dist/facebook-source-20260915-191443.zip`，98个清单文件、418480字节。ZIP完整性、精确成员集合、全部SHA256SUMS和当前源码字节一致性检查通过；无发布文件被Git忽略。

本次不提交或推送Git，不删除历史数据。
