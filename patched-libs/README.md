# patched-libs 事实说明

**这三个文件不是补丁版，就是原始库。**

2026-10-30 实测（`sha256sum`）：

| 文件 | SHA-256 |
| --- | --- |
| `x86_64/libcoldstart.so` | `846a81f2105dae136efddac8689990ca…6ad20` |
| 设备 `/data/data/com.facebook.katana/lib-compressed/libcoldstart.so` | 同上 |
| 设备 `/data/local/tmp/libcoldstart.patched` | 同上 |

三者**逐字节相同**。设备上那个 `.patched` 后缀文件也只是原始库的副本，
`X509_verify_cert` 反汇编出来是完整的 BoringSSL 实现（596 字节，
`pushq %rbp; …; callq X509_free; …`），没有任何短路补丁。

## 为什么会有这个目录

历史流程曾把库从设备 `lib-compressed/` 拉出来、改名为 `*.patched` 再 push 回去，
但从未真正修改字节。当前 HTTPS 解密依赖的是：

1. `facebook_ssl_unpin.js` 的 Frida hook（`X509_verify_cert`，global + `libcoldstart.so`）
2. mitmproxy CA 装进 system store 并 bind 到 `/apex/com.android.conscrypt/cacerts`

也就是说：**不要以为这里已经有补丁，从而跳过 Frida attach。** 跳过就抓不到明文。

## 样本来源

- `libcoldstart.so` 不在 `base.apk` 里（APK 的 `lib/x86_64/` 只有 16 个 ≤0.4MB 的小 so），
  由 Facebook 的 Superpack 在运行时解压到
  `/data/data/com.facebook.katana/lib-compressed/`。
- 因此无法从 APK 复现这个文件；只能从设备拉取，并记录包版本与哈希。
- 当前设备：`com.facebook.katana` `555.0.0.49.59`，versionCode `470015241`，
  base.apk SHA-256 `93e8c9e0e8dc533f858437739fb5bfc81c501bfae22dc14ac24d48665e19871b`。

## 如果要真做补丁

用 `LIEF` 或 `patchelf` 修改后再记录前后哈希，并同步更新
`STABLE_BASELINE.md`；否则保持现状（Frida hook）即可。
