# Facebook Capture API 使用说明

本文描述当前 `facebook_capture_api.py` 已实现的本地 HTTP 合约，以及
`facebook_api_client.py` 的最小用法。它不代表任何一次真实 Facebook 抓取已经成功；
真实结果仍须看 job 的终态、`error`、`completion_scope`、完整性报告和 artifact 内容。

## 启动准备

在保存有本项目依赖和设备配置的机器上启动服务：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe facebook_capture_api.py --host 127.0.0.1 --port 8765
```

服务只接受 loopback bind；它会拒绝把监听地址改成公网或局域网地址。客户端默认使用
`http://127.0.0.1:8765`。`requests` 已列入项目依赖；独立客户端环境可只安装
`python -m pip install requests==2.32.5`。macOS 的虚拟环境解释器为 `.venv/bin/python`。
首次下载仍须按 [README](README.md#下载后如何开始) 准备 SDK、专用模拟器、Helper、证书和人工登录；API 不会替用户完成账号验证。Windows 完整启动也可运行 `START.cmd`，端口在 `facebook.local.json` 中配置。不要选择模拟器已占用的 `5555` 作为 API 端口。

这个服务是单执行器、串行队列（`health.mode` 为 `primary-standby-serial`），不是多租户
服务。primary/standby 是 worker 故障转移，不是并行租户隔离；不要把它直接暴露给不受信任
的网络。

## 三步 requests 示例

下面是与现有路由和字段一致的三步流程。`reaction_scroll_limit` 被省略时保持服务端的
全量/terminal 默认语义；只有明确传整数才是 bounded sample。

```python
import time
import requests

base = "http://127.0.0.1:8765"
session = requests.Session()

# 1. POST /v1/jobs：只提交一次；202 返回 job_id。
submitted = session.post(
    f"{base}/v1/jobs",
    json={"post_url": "https://www.facebook.com/example/posts/1"},
    timeout=30,
)
submitted.raise_for_status()
job_id = submitted.json()["job_id"]

# 2. GET /v1/jobs/{job_id}：只轮询 GET；不要在超时后自动 POST 重复提交。
deadline = time.monotonic() + 900
while True:
    if time.monotonic() >= deadline:
        raise TimeoutError(f"job {job_id} still running; it was not cancelled")
    status = session.get(f"{base}/v1/jobs/{job_id}", timeout=30)
    status.raise_for_status()
    job = status.json()
    if job["status"] == "completed":
        break
    if job["status"] in {"failed", "cancelled"}:
        raise RuntimeError(f"job {job_id}: {job.get('error') or job['status']}")
    if job["status"] not in {"queued", "running", "cancelling"}:
        raise RuntimeError(f"unknown job status: {job['status']!r}")
    time.sleep(2)

# 3. GET /v1/jobs/{job_id}/result：仅 completed 才读取结构化结果。
result = session.get(f"{base}/v1/jobs/{job_id}/result", timeout=30)
result.raise_for_status()
payload = result.json()
print(payload["completion_scope"], payload["reaction_users"])
```

更稳妥的 Python 调用是：

```python
from facebook_api_client import FacebookApiClient

with FacebookApiClient(base, session=session) as client:
    job = client.submit(post_url="https://www.facebook.com/example/posts/1")
    completed = client.wait(job["job_id"], overall_timeout=900, poll_interval=2)
    result = client.result(completed["job_id"])
```

`FacebookApiClient` 不会自动重试 POST，也不会把 wait 超时转成 cancel。`session` 可以是
已有的认证 Session，`headers` 可以附加受保护转发所需的认证头：

```python
client = FacebookApiClient(
    "https://private-forwarder.example",
    session=session,
    headers={"Authorization": "Bearer <short-lived-token>"},
)
```

不要把真实 token 写入日志、文档或提交记录。

客户端 `timeout` 使用 requests 的 connect/read timeout 语义：单个数字同时作为连接和读取
超时，二元组可分别指定 `(connect, read)`；它不是严格的总墙钟截止。`wait()` 的
`overall_timeout` 是客户端本地轮询截止时间，`0` 表示执行一次即时状态检查后快速超时；
一次正在进行的 HTTP 请求仍可能受其 read timeout 语义影响而稍后返回。`poll_interval` 必须
是有限的正数，避免忙轮询；`overall_timeout` 必须是有限的非负数。

## 状态、错误和 HTTP

当前服务实际产生的单 job 状态是：

| 状态 | 客户端行为 |
| --- | --- |
| `queued` | 继续 GET 轮询 |
| `running` | 继续 GET 轮询 |
| `completed` | `wait()` 返回；随后可 GET `result` |
| `failed` | `wait()` 抛 `FacebookJobError`，包含 `job_id` 和服务 `error` |
| `cancelled` | `wait()` 抛 `FacebookJobError`，包含 `job_id` 和服务 `error`/状态 |

客户端也把 `cancelling` 作为可轮询状态，以兼容受保护转发层的中间状态；当前本地服务
的 `cancel()` 会直接持久化为 `cancelled`。任何其他字符串（包括空缺状态）都会抛
`FacebookUnknownStatusError`，不会无限轮询。

当前单 job 路由的 HTTP 合约：

| 请求 | 成功 | 当前错误 |
| --- | --- | --- |
| `POST /v1/jobs` | `202`，JSON 含 `job_id` | `400`：JSON/字段/大小无效 |
| `GET /v1/jobs/{id}` | `200`，job 状态 | `404`：job 不存在 |
| `GET /v1/jobs/{id}/result` | `200`，仅 completed 可取 | `404`：不存在；`409`：尚未 completed 或服务结果错误 |
| `GET /v1/jobs/{id}/artifacts/{name}?download=1` | `200`，二进制内容 | `404`：job/name 不存在、未选中或尚未可用 |
| `POST /v1/jobs/{id}/cancel` | `200`，含 `cancelled`/`reason`/`job` | `404`：job 不存在 |

客户端还会明确区分：连接/读取 timeout 等 `FacebookApiTransportError`、非 2xx 的
`FacebookApiHttpError`、成功但非 JSON 的 `FacebookApiProtocolError`、总等待时间耗尽的
`FacebookWaitTimeoutError`。这些异常都不会暗中重复提交。HTTP 非 JSON 错误的响应正文只
用于有限长度诊断，不会被当作成功结果。

## 结果完整性

completed 是服务任务的终态，不等于客户端可以跳过完整性检查。结果 JSON 的
`completion_scope` 有两种当前值：

* `terminal`：服务端通过当前解析/分页完整性条件，允许作终止分页结果声明。
* `bounded-sample`：调用方显式传了 `reaction_scroll_limit`，结果在 cap 处停止；这是有界样本，
  不能描述为全量。

`partial_evidence_available` 出现在 job 状态中，表示磁盘上有安全抓取证据；它不是成功或
全量声明。`failed`/`cancelled` 也可能保留 checkpoint、CSV 或安全 flow，但必须按失败/取消
处理，不能调用 `result()` 伪装成 completed。限流、open-tail、目标不匹配、解析错误等应以
服务返回的 `error`、`completeness`、`transport_observation` 和报告文件为准；本客户端不把
HTTP 200 或空列表自动升级为真实抓取成功。

## Artifact 下载

artifact 路由接收逻辑名称，不接收服务端返回的本机路径。常用结果文件是：

* `facebook-sample-patterns.json`
* `facebook-reactor-users.csv`

当前服务的完整单 job artifact 名称还包括：`raw-network.pcap`、
`raw-network-capture.json`、`raw-facebook-http.mitm`、`raw-facebook-http-index.jsonl`、
`raw-facebook-http-summary.json`、`safe-facebook-http-capture.jsonl`、
`facebook-live-progress.json`。其中 `safe_capture`、`progress`、`pcap`、`mitm` 是提交时的
可选保存组名称，不是下载 URL 中的 artifact 名。
逻辑名 `facebook-reactor-users.csv` 在服务端内部才映射到 `analysis/facebook-reactor-users.csv`；
调用方不要拼接 `analysis/`。

```python
client.download(
    job_id,
    "facebook-reactor-users.csv",
    "output/facebook-reactor-users.csv",
)
```

下载先写同目录随机临时文件，校验响应长度后再 `os.replace`。连接中断、长度不符、HTTP
错误或空响应会抛 `FacebookApiDownloadError`，不会留下半个目标文件；若目标已存在，失败时
保留原文件。为避免 requests 自动解压 gzip 后把解压长度误与压缩 `Content-Length` 比较，
当前客户端只接受未编码或 `identity` 响应；收到其他 `Content-Encoding` 会明确拒绝并保留
完整性检查。服务返回的 `run_dir`、`job_log` 等本机路径仅是诊断信息，客户端不会读取它们。

## 取消和重启恢复

显式取消：

```python
reply = client.cancel(job_id)
```

服务对 `queued`/`running` 记录取消并尝试停止进程；对已终态任务返回“已经是该状态”的
说明。客户端 wait 超时不会调用这个接口，是否取消必须由调用方明确决定。

job manifest 持久化在服务端 `api-jobs/fb_*.job.json`。服务重启时：

* `queued` 任务在没有遗留进程证据时恢复入队；
* `running` 任务在确认 capture lease 已清理时转为 `queued`，并标记
  `interrupted by API service restart; safely requeued`，保留 checkpoint/attempt 记录；
* 如果旧进程仍在运行，或 PID 身份无法验证，服务不会自动接管，任务会变为 `failed`，并阻塞
  worker takeover；
* 已完成、失败、取消任务从 manifest 恢复其可见状态。恢复不是客户端重复 POST。

因此，服务重启后可用同一个 `job_id` 调用 `wait()`/`get_status()`；不要因为客户端进程
退出就重新提交同一个 URL。

## 远程私有转发

不要为了远程访问修改服务的 `--host`。在受控机器上保持 loopback，然后使用 SSH 本地端口
转发或已经认证的私有隧道，例如：

```powershell
ssh -N -L 8765:127.0.0.1:8765 user@capture-host
```

本机客户端仍使用 `http://127.0.0.1:8765`；隧道、认证头、ACL 和审计由转发层负责。若
使用 HTTPS 私有 forwarder，则把 `base_url` 指向该私有地址并通过 `Session`/`headers` 传递
认证，不要把 API 监听到公网。

## CLI

新任务（默认全量语义）：

```powershell
python facebook_api_client.py "https://www.facebook.com/example/posts/1" `
  --base-url http://127.0.0.1:8765 `
  --output-dir .\client-output
```

显式 bounded sample：

```powershell
python facebook_api_client.py "https://www.facebook.com/example/posts/1" `
  --reaction-scroll-limit 20 --output-dir .\client-output
```

恢复已有任务（不再 POST）：

```powershell
python facebook_api_client.py --job-id fb_0123456789abcdef `
  --output-dir .\client-output
```

CLI 在提交后会立即 flush 一行只含 `job_id`/`status` 的 JSON，便于超时或中断后找回任务；
完成后再保存 `result.json` 并下载 `facebook-reactor-users.csv`。它不会把认证 header 打印
出来，也不会声称真实抓取成功；请检查最终输出中的 `completion_scope` 和结果完整性字段。

## 伪代码参数修正

| 不准确的写法 | 当前合约 |
| --- | --- |
| `submit(url=...)` | `submit(post_url=...)`，或 HTTP JSON 的 `post_url` |
| `max_scrolls=20` | `reaction_scroll_limit=20`；省略即不改变全量默认 |
| artifact `analysis/facebook-reactor-users.csv` | artifact 名 `facebook-reactor-users.csv` |
| `wait(timeout=...)` | `wait(overall_timeout=..., poll_interval=...)`；HTTP `timeout` 是单次请求超时 |
| 超时后重新 POST | 保留原 `job_id`，先 `get_status()`；如需停止再明确 `cancel()` |
