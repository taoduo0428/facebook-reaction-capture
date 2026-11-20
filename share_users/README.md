# 分享／转发用户采集材料

此目录保留分享用户采集的来源、正式文件哈希和历史测试索引。

## 运行方式

统一从项目根目录运行 `START.cmd`。它启动 API（8765）和网页总台（8766），不要从本目录另起一套 API。

## 正式源码

- [分享结果契约](../facebook_share_contract.py)
- [群组内联入口](../facebook_group_inline_route.py)
- [分享链接解析](../resolve_facebook_share_url.py)
- [分享结果验证工具](../tools/verify_share_result.py)

`share_users_manifest.json` 记录上述文件的路径、SHA-256 及历史来源。正式源码仅保留一份，不再维护 `core/` 镜像；修改正式文件后更新索引中的哈希即可。

manifest 中的外部候选测试路径是历史来源记录，不代表文件当前仍可用，也不代表当前版本已通过那些测试。
