from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SERIAL = "emulator-5554"
DEFAULT_AVD_NAME = "fb-x86_64-api35"
DEFAULT_CAPTURE_PORT = 8081
DEFAULT_NATIVE_PORT = 18766
WORKER_CONFIG_NAME = "facebook_workers.json"
MAX_REQUEST_BYTES = 64 * 1024
MAX_BATCH_POSTS = 100
GRAPH_API_VERSION = "v26.0"
REACTION_DEFAULTS = {
    "ReactionScrollCount": 60,
    "ReactionAbsoluteCap": 0,
    "ReactionExtensionChunk": 12,
    "ReactionTabLimit": 8,
    "ReactionScreenScrollCount": 2,
    "ReactionProgressPollIntervalMs": 500,
    "ReactionProgressPollTimeoutMs": 2200,
    "ReactionLiveProgressPollIntervalMs": 150,
    "ReactionLiveProgressPollTimeoutMs": 1200,
    "ReactionSteadyNoGrowthPollTimeoutMs": 500,
    "ReactionLongSwipeStartY": 1700,
    "ReactionLongSwipeEndY": 250,
    "ReactionLongSwipeDurationMs": 300,
    "ReactionPipelineBurstSize": 2,
    "ReactionPipelineSwipeGapMs": 150,
    "ReactionNativeScrollEnabled": 1,
    "ReactionNativeScrollTimeoutMs": 2500,
    "ReactionUiDumpEveryN": 64,
    "ReactionMinCycleMs": 700,
    "ReactionInitialPaginationCooldownSeconds": 4,
    "ReactionNoGrowthUiDumpEveryN": 1,
    "MaxFlows": 0,
}

STATIC_ASSETS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}

ARTIFACT_SPECS = (
    ("raw-network.pcap", "原始网络 PCAP", "TLS 解密前", "raw-network.pcap"),
    ("raw-network-capture.json", "PCAP 校验信息", "完整性", "raw-network-capture.json"),
    ("raw-facebook-http.mitm", "完整 MITM flow", "实时解密", "raw-facebook-http.mitm"),
    ("raw-facebook-http-index.jsonl", "安全 flow 索引", "实时解密", "raw-facebook-http-index.jsonl"),
    ("safe-facebook-http-capture.jsonl", "脱敏 GraphQL 记录", "安全分析", "safe-facebook-http-capture.jsonl"),
    ("facebook-live-progress.json", "分页实时状态", "抓取进度", "facebook-live-progress.json"),
    ("raw-facebook-http-summary.json", "MITM 校验摘要", "完整性", "raw-facebook-http-summary.json"),
    ("facebook-sample-patterns.json", "解析与完整性报告", "解析结果", "analysis/facebook-sample-patterns.json"),
    ("facebook-reactor-users.csv", "Reaction 用户结果", "解析结果", "analysis/facebook-reactor-users.csv"),
)

ARTIFACT_PATHS = {name: Path(relative) for name, _label, _layer, relative in ARTIFACT_SPECS}
INLINE_ARTIFACT_SUFFIXES = {".csv", ".json", ".jsonl", ".log", ".txt"}
OPTIONAL_ARTIFACT_GROUPS = {
    "pcap": {"raw-network.pcap", "raw-network-capture.json"},
    "mitm": {"raw-facebook-http.mitm", "raw-facebook-http-index.jsonl", "raw-facebook-http-summary.json"},
    "safe_capture": {"safe-facebook-http-capture.jsonl"},
    "progress": {"facebook-live-progress.json"},
}
DEFAULT_SAVE_ARTIFACTS = tuple(OPTIONAL_ARTIFACT_GROUPS)
REQUIRED_ARTIFACTS = {"facebook-sample-patterns.json", "facebook-reactor-users.csv"}
ARTIFACT_GROUP_BY_NAME = {
    name: group
    for group, names in OPTIONAL_ARTIFACT_GROUPS.items()
    for name in names
}
BATCH_ARTIFACT_SPECS = (
    ("batch-summary.json", "批次汇总", "批次结果"),
    ("batch-posts.csv", "帖子任务明细", "批次结果"),
    ("batch-reactor-users.csv", "跨帖子去重用户", "批次结果"),
    ("batch-user-post-relations.csv", "用户与帖子关系", "批次结果"),
)


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool
    cooldown_seconds: int = 900
    frida_ssl_unpin: bool = False


def _default_worker(serial: str = DEFAULT_SERIAL) -> WorkerConfig:
    return WorkerConfig(
        worker_id="worker-01",
        serial=serial,
        avd_name=DEFAULT_AVD_NAME,
        capture_port=DEFAULT_CAPTURE_PORT,
        native_port=DEFAULT_NATIVE_PORT,
        enabled=True,
    )


def validate_worker_config(workers: list[WorkerConfig]) -> list[WorkerConfig]:
    if not workers:
        raise ApiError("worker configuration must contain at least one worker")
    if not any(worker.enabled for worker in workers):
        raise ApiError("worker configuration must contain at least one enabled worker")
    unique_fields = ("worker_id", "serial", "avd_name")
    for field in unique_fields:
        values = [getattr(worker, field) for worker in workers]
        if len(values) != len(set(values)):
            raise ApiError(f"worker configuration has duplicate {field}")
    for worker in workers:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", worker.worker_id):
            raise ApiError(f"invalid worker_id: {worker.worker_id!r}")
        if not worker.serial.strip() or not worker.avd_name.strip():
            raise ApiError(f"worker {worker.worker_id} requires serial and avd_name")
        for name, port in (("capture_port", worker.capture_port), ("native_port", worker.native_port)):
            if not 1024 <= port <= 65535:
                raise ApiError(f"worker {worker.worker_id} has invalid {name}: {port}")
        if worker.capture_port == worker.native_port:
            raise ApiError(f"worker {worker.worker_id} capture_port and native_port must differ")
        if worker.cooldown_seconds < 0:
            raise ApiError(f"worker {worker.worker_id} cooldown_seconds cannot be negative")
    all_ports = [port for worker in workers for port in (worker.capture_port, worker.native_port)]
    if len(all_ports) != len(set(all_ports)):
        raise ApiError("worker capture and native ports must be globally unique")
    return workers


def load_worker_config(path: Path | None = None, fallback_serial: str = DEFAULT_SERIAL) -> list[WorkerConfig]:
    config_path = path or (BASE_DIR / WORKER_CONFIG_NAME)
    if not config_path.is_file():
        return [_default_worker(fallback_serial)]
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(f"worker configuration could not be read: {config_path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ApiError("worker configuration version must be 1")
    raw_workers = payload.get("workers")
    if not isinstance(raw_workers, list):
        raise ApiError("worker configuration workers must be a list")
    workers: list[WorkerConfig] = []
    try:
        for raw in raw_workers:
            if not isinstance(raw, dict):
                raise TypeError("worker entry must be an object")
            enabled = raw["enabled"]
            if not isinstance(enabled, bool):
                raise TypeError("worker enabled must be boolean")
            frida_ssl_unpin = raw.get("frida_ssl_unpin", False)
            if not isinstance(frida_ssl_unpin, bool):
                raise TypeError("worker frida_ssl_unpin must be boolean")
            workers.append(
                WorkerConfig(
                    worker_id=str(raw["worker_id"]).strip(),
                    serial=str(raw["serial"]).strip(),
                    avd_name=str(raw["avd_name"]).strip(),
                    capture_port=int(raw["capture_port"]),
                    native_port=int(raw["native_port"]),
                    enabled=enabled,
                    cooldown_seconds=int(raw.get("cooldown_seconds", 900)),
                    frida_ssl_unpin=frida_ssl_unpin,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("worker configuration contains an invalid worker entry") from exc
    return validate_worker_config(workers)


def write_worker_config(path: Path, workers: list[WorkerConfig]) -> None:
    validated = validate_worker_config(workers)
    _atomic_write_json(path, {"version": 1, "workers": [asdict(worker) for worker in validated]})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_manifest_path(job_id: str) -> Path:
    return BASE_DIR / "api-jobs" / f"{job_id}.job.json"


def _batch_manifest_path(batch_id: str) -> Path:
    return BASE_DIR / "api-batches" / f"{batch_id}.batch.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def normalize_post_url(value: str) -> str:
    value = str(value or "").strip().replace("\\u0026", "&")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("post_url must be an absolute http(s) URL")
    if "facebook.com" not in parsed.netloc.lower():
        raise ApiError("post_url must be a Facebook URL")
    return value


def normalize_collect(value: Any) -> list[str]:
    if value is None:
        return ["reactions"]
    if not isinstance(value, list):
        raise ApiError("collect must be a list")
    allowed = {"reactions", "shares"}
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in allowed:
            raise ApiError(f"unsupported collect item: {name or item}")
        if name not in result:
            result.append(name)
    if not result:
        raise ApiError("collect cannot be empty")
    return result


def normalize_save_artifacts(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_SAVE_ARTIFACTS)
    if not isinstance(value, list):
        raise ApiError("save_artifacts must be a list")
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in OPTIONAL_ARTIFACT_GROUPS:
            raise ApiError(f"unsupported artifact group: {name or item}")
        if name not in result:
            result.append(name)
    return result


def normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    post_url = normalize_post_url(payload.get("post_url", ""))
    post_id = str(payload.get("post_id") or "").strip()
    if not post_url and not post_id:
        raise ApiError("post_url or post_id is required")
    if post_id and not re.fullmatch(r"[A-Za-z0-9_.:-]+", post_id):
        raise ApiError("post_id contains unsupported characters")
    return {
        "post_url": post_url,
        "post_id": post_id,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
    }


def normalize_batch_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    values = payload.get("post_urls")
    if not isinstance(values, list):
        raise ApiError("post_urls must be a list")
    post_urls: list[str] = []
    for value in values:
        url = normalize_post_url(str(value or ""))
        if url and url not in post_urls:
            post_urls.append(url)
    if not post_urls:
        raise ApiError("post_urls cannot be empty")
    if len(post_urls) > MAX_BATCH_POSTS:
        raise ApiError(f"a batch can contain at most {MAX_BATCH_POSTS} unique posts")
    return {
        "post_urls": post_urls,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
    }


def _which(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise ApiError(f"required executable not found on PATH: {name}")
    return value


def _run_quiet(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )


def adb_snapshot(serial: str = DEFAULT_SERIAL) -> dict[str, Any]:
    adb = shutil.which("adb")
    if not adb:
        return {"state": "adb-not-found", "boot_completed": False, "proxy": ""}
    try:
        state = _run_quiet([adb, "-s", serial, "get-state"], 5)
        if state.returncode != 0 or state.stdout.strip() != "device":
            return {"state": "offline", "boot_completed": False, "proxy": ""}
        boot = _run_quiet([adb, "-s", serial, "shell", "getprop", "sys.boot_completed"], 5)
        proxy = _run_quiet([adb, "-s", serial, "shell", "settings", "get", "global", "http_proxy"], 5)
        return {
            "state": "device",
            "boot_completed": boot.stdout.strip() == "1",
            "proxy": proxy.stdout.strip(),
        }
    except (OSError, subprocess.SubprocessError):
        return {"state": "offline", "boot_completed": False, "proxy": ""}


def ensure_emulator(
    serial: str = DEFAULT_SERIAL,
    timeout_seconds: int = 180,
    *,
    worker: WorkerConfig | None = None,
    avd_name: str | None = None,
    capture_port: int = DEFAULT_CAPTURE_PORT,
    native_port: int = DEFAULT_NATIVE_PORT,
) -> dict[str, Any]:
    if worker is not None:
        serial = worker.serial
        avd_name = worker.avd_name
        capture_port = worker.capture_port
        native_port = worker.native_port
    avd_name = avd_name or DEFAULT_AVD_NAME
    snapshot = adb_snapshot(serial)
    if snapshot["state"] == "device" and snapshot["boot_completed"]:
        return snapshot
    script = BASE_DIR / "start_facebook_stable.ps1"
    if not script.exists():
        raise ApiError(f"emulator bootstrap script not found: {script}")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise ApiError("PowerShell is required to start the current Windows Facebook worker")
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    bootstrap_log = BASE_DIR / "api-jobs" / "emulator-bootstrap.log"
    bootstrap_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = bootstrap_log.open("a", encoding="utf-8")
    try:
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Serial",
            serial,
            "-AvdName",
            avd_name,
            "-CapturePort",
            str(capture_port),
            "-NativePort",
            str(native_port),
        ]
        if worker is not None and worker.frida_ssl_unpin:
            command.append("-RestoreMitmCertificate")
        subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    finally:
        log_handle.close()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = adb_snapshot(serial)
        if snapshot["state"] == "device" and snapshot["boot_completed"]:
            return snapshot
        time.sleep(2)
    raise ApiError(f"Android emulator did not become ready within {timeout_seconds}s")


def build_capture_command(
    request: dict[str, Any],
    serial: str = DEFAULT_SERIAL,
    run_dir: Path | None = None,
    *,
    proxy_port: int = DEFAULT_CAPTURE_PORT,
    native_port: int = DEFAULT_NATIVE_PORT,
    frida_ssl_unpin: bool = False,
) -> list[str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    script = BASE_DIR / "run_facebook_capture.ps1"
    args = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Serial",
        serial,
        "-ProxyPort",
        str(proxy_port),
        "-DurationSeconds",
        "0",
        "-ScrollCount",
        "0",
        "-ScrollDelaySeconds",
        "0",
        "-ReadOnlyWaitSeconds",
        "3",
        "-AutoScreenThenDeep",
        "-ReactionScrollCount",
        str(REACTION_DEFAULTS["ReactionScrollCount"]),
        "-ReactionAbsoluteCap",
        str(REACTION_DEFAULTS["ReactionAbsoluteCap"]),
        "-ReactionExtensionChunk",
        str(REACTION_DEFAULTS["ReactionExtensionChunk"]),
        "-ReactionTabLimit",
        str(REACTION_DEFAULTS["ReactionTabLimit"]),
        "-ReactionScreenScrollCount",
        str(REACTION_DEFAULTS["ReactionScreenScrollCount"]),
        "-ReactionProgressPollIntervalMs",
        str(REACTION_DEFAULTS["ReactionProgressPollIntervalMs"]),
        "-ReactionProgressPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionProgressPollTimeoutMs"]),
        "-ReactionLiveProgressPollIntervalMs",
        str(REACTION_DEFAULTS["ReactionLiveProgressPollIntervalMs"]),
        "-ReactionLiveProgressPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionLiveProgressPollTimeoutMs"]),
        "-ReactionSteadyNoGrowthPollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionSteadyNoGrowthPollTimeoutMs"]),
        "-ReactionLongSwipeStartY",
        str(REACTION_DEFAULTS["ReactionLongSwipeStartY"]),
        "-ReactionLongSwipeEndY",
        str(REACTION_DEFAULTS["ReactionLongSwipeEndY"]),
        "-ReactionLongSwipeDurationMs",
        str(REACTION_DEFAULTS["ReactionLongSwipeDurationMs"]),
        "-ReactionPipelineBurstSize",
        str(REACTION_DEFAULTS["ReactionPipelineBurstSize"]),
        "-ReactionPipelineSwipeGapMs",
        str(REACTION_DEFAULTS["ReactionPipelineSwipeGapMs"]),
        "-ReactionNativeScrollEnabled",
        str(REACTION_DEFAULTS["ReactionNativeScrollEnabled"]),
        "-ReactionNativeScrollTimeoutMs",
        str(REACTION_DEFAULTS["ReactionNativeScrollTimeoutMs"]),
        "-ReactionNativeScrollPort",
        str(native_port),
        "-ReactionUiDumpEveryN",
        str(REACTION_DEFAULTS["ReactionUiDumpEveryN"]),
        "-ReactionMinCycleMs",
        str(REACTION_DEFAULTS["ReactionMinCycleMs"]),
        "-ReactionInitialPaginationCooldownSeconds",
        str(REACTION_DEFAULTS["ReactionInitialPaginationCooldownSeconds"]),
        "-ReactionNoGrowthUiDumpEveryN",
        str(REACTION_DEFAULTS["ReactionNoGrowthUiDumpEveryN"]),
        "-MaxFlows",
        str(REACTION_DEFAULTS["MaxFlows"]),
    ]
    if "pcap" in set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS)):
        args.append("-EnableRawNetworkCapture")
    if frida_ssl_unpin:
        args.append("-EnableFridaSslUnpin")
    if run_dir is not None:
        args += ["-OutputDir", str(run_dir)]
    if request.get("post_url"):
        args += ["-PostUrl", request["post_url"]]
    if request.get("post_id"):
        args += ["-PostId", request["post_id"]]
    collect = set(request["collect"])
    if "reactions" in collect:
        args.append("-OpenReactionList")
    if "shares" in collect:
        args.append("-OpenShareList")
        args += ["-ShareScrollCount", "40"]
    return args


def _find_run_dir(log_text: str, started_epoch: float) -> Path | None:
    matches = re.findall(r"Capture finished:\s*(.+)", log_text)
    for raw in reversed(matches):
        candidate = Path(raw.strip().strip('"'))
        if candidate.exists():
            return candidate
    runs = BASE_DIR / "runs"
    if not runs.exists():
        return None
    candidates = [p for p in runs.iterdir() if p.is_dir() and p.stat().st_mtime >= started_epoch - 2]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def resolve_static_asset(request_path: str) -> Path | None:
    name = STATIC_ASSETS.get(urlparse(request_path).path)
    return WEB_DIR / name if name else None


def _artifact_metadata(
    run_dir: Path,
    job_status: str,
    save_artifacts: list[str] | None = None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    selected_groups = set(save_artifacts if save_artifacts is not None else DEFAULT_SAVE_ARTIFACTS)
    for name, label, layer, relative in ARTIFACT_SPECS:
        path = run_dir / Path(relative)
        group = ARTIFACT_GROUP_BY_NAME.get(name)
        selected = name in REQUIRED_ARTIFACTS or group in selected_groups
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        if not selected:
            state = "discarding" if job_status in {"queued", "running"} else "not_saved"
            size = 0
        elif size > 0:
            state = "ready" if job_status in {"completed", "failed", "cancelled"} else "writing"
        else:
            state = "missing" if job_status == "completed" else "waiting"
        artifacts.append(
            {
                "name": name,
                "label": label,
                "layer": layer,
                "path": str(path),
                "size_bytes": size,
                "state": state,
                "viewable": path.suffix.lower() in INLINE_ARTIFACT_SUFFIXES,
                "url": f"/v1/jobs/{{job_id}}/artifacts/{name}",
                "required": name in REQUIRED_ARTIFACTS,
                "selected": selected,
            }
        )
    return artifacts


def _valid_reactor_user_count(run_dir: Path) -> int:
    path = run_dir / "analysis" / "facebook-reactor-users.csv"
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for row in csv.DictReader(handle) if str(row.get("user_id") or "").strip())
    except OSError:
        return 0


def write_batch_outputs(batch_dir: Path, batch_id: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    batch_dir.mkdir(parents=True, exist_ok=True)
    post_rows: list[dict[str, Any]] = []
    user_rows_by_id: dict[str, dict[str, Any]] = {}
    relation_rows: list[dict[str, Any]] = []
    relation_keys: set[tuple[str, str]] = set()
    per_post_valid_sum = 0
    status_counts = {name: 0 for name in ("queued", "running", "completed", "failed", "cancelled")}

    for index, job in enumerate(jobs, start=1):
        status = str(job.get("status") or "queued")
        if status in status_counts:
            status_counts[status] += 1
        request = job.get("request") or {}
        post_url = str(request.get("post_url") or "")
        run_dir_text = str(job.get("run_dir") or "")
        run_dir = Path(run_dir_text) if run_dir_text else Path()
        rows = _read_csv(run_dir / "analysis" / "facebook-reactor-users.csv") if run_dir_text else []
        post_user_ids: set[str] = set()
        for row in rows:
            user_id = str(row.get("user_id") or "").strip()
            if not user_id:
                continue
            post_user_ids.add(user_id)
            if user_id not in user_rows_by_id:
                user_rows_by_id[user_id] = {
                    "user_id": user_id,
                    "user_name": str(row.get("user_name") or ""),
                    "avatar_url": str(row.get("avatar_url") or ""),
                    "post_count": 0,
                }
            relation_key = (user_id, str(job.get("job_id") or ""))
            if relation_key not in relation_keys:
                relation_keys.add(relation_key)
                relation_rows.append(
                    {
                        "user_id": user_id,
                        "job_id": relation_key[1],
                        "post_index": index,
                        "post_url": post_url,
                        "source_post_id": str(row.get("source_post_id") or ""),
                        "reaction_id": str(row.get("reaction_id") or ""),
                    }
                )
        for user_id in post_user_ids:
            user_rows_by_id[user_id]["post_count"] += 1
        valid_users = len(post_user_ids)
        per_post_valid_sum += valid_users
        metrics = _reaction_progress(run_dir) if run_dir_text else {}
        post_rows.append(
            {
                "post_index": index,
                "job_id": str(job.get("job_id") or ""),
                "post_url": post_url,
                "status": status,
                "valid_users": valid_users,
                "reaction_pages": int(metrics.get("reaction_pages") or 0),
                "run_dir": run_dir_text,
            }
        )

    user_rows = sorted(user_rows_by_id.values(), key=lambda row: row["user_id"])
    relation_rows.sort(key=lambda row: (int(row["post_index"]), row["user_id"]))
    terminal_count = status_counts["completed"] + status_counts["failed"] + status_counts["cancelled"]
    if status_counts["running"]:
        batch_status = "running"
    elif status_counts["queued"]:
        batch_status = "queued"
    elif terminal_count == len(jobs) and (status_counts["failed"] or status_counts["cancelled"]):
        batch_status = "completed_with_errors"
    elif terminal_count == len(jobs):
        batch_status = "completed"
    else:
        batch_status = "queued"
    summary = {
        "batch_id": batch_id,
        "status": batch_status,
        "updated_at": utc_now(),
        "total_posts": len(jobs),
        "status_counts": status_counts,
        "per_post_valid_sum": per_post_valid_sum,
        "cross_post_unique_users": len(user_rows),
        "cross_post_duplicate_occurrences": max(0, per_post_valid_sum - len(user_rows)),
    }
    _atomic_write_csv(
        batch_dir / "batch-posts.csv",
        ["post_index", "job_id", "post_url", "status", "valid_users", "reaction_pages", "run_dir"],
        post_rows,
    )
    _atomic_write_csv(
        batch_dir / "batch-reactor-users.csv",
        ["user_id", "user_name", "avatar_url", "post_count"],
        user_rows,
    )
    _atomic_write_csv(
        batch_dir / "batch-user-post-relations.csv",
        ["user_id", "job_id", "post_index", "post_url", "source_post_id", "reaction_id"],
        relation_rows,
    )
    _atomic_write_json(batch_dir / "batch-summary.json", summary)
    return summary


def _reaction_progress(run_dir: Path) -> dict[str, Any]:
    live = _read_json(run_dir / "facebook-live-progress.json")
    sources = (((live.get("families") or {}).get("reaction") or {}).get("sources") or {})
    candidates = [value for value in sources.values() if isinstance(value, dict)] if isinstance(sources, dict) else []
    source = max(candidates, key=lambda item: int(item.get("user_count") or 0), default={})
    diagnostics = _read_json(run_dir / "readonly-ui" / "exploration-diagnostics.json")
    pagination_runs = [item for item in diagnostics.get("pagination_runs") or [] if isinstance(item, dict)]
    pagination = pagination_runs[-1] if pagination_runs else {}
    report = _read_json(run_dir / "analysis" / "facebook-sample-patterns.json")
    integrity = report.get("reaction_integrity") or {}
    enumeration = ((report.get("enumeration_status") or {}).get("reactions") or {})
    return {
        "reaction_users": int(source.get("user_count") or enumeration.get("enumerated_identity_count") or integrity.get("unique_reactor_identities") or 0),
        "reaction_pages": int(source.get("page_count") or integrity.get("successful_page_count") or 0),
        "query_records": int(source.get("query_records") or 0),
        "terminal": bool(source.get("terminal_page_seen") or pagination.get("terminal_seen") or integrity.get("terminal_page_seen")),
        "rate_limits": int(source.get("rate_limit_count") or 0),
        "record_lines": int(live.get("record_line_count") or 0),
        "attempts": int(pagination.get("attempts") or 0),
        "growth_events": int(pagination.get("growth_events") or 0),
        "elapsed_ms": int(pagination.get("elapsed_ms") or 0),
        "stop_reason": str(pagination.get("stop_reason") or ""),
        "updated_at": live.get("updated_at"),
    }


def _stage(stage_id: str, label: str, state: str, detail: str) -> dict[str, str]:
    return {"id": stage_id, "label": label, "state": state, "detail": detail}


def build_progress_snapshot(
    run_dir: Path,
    job_status: str,
    android: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    android = android or {}
    request = request or {}
    metrics = _reaction_progress(run_dir)
    metrics["valid_reactor_users"] = _valid_reactor_user_count(run_dir)
    artifacts = _artifact_metadata(run_dir, job_status, request.get("save_artifacts"))
    ready = {item["name"]: item["size_bytes"] > 0 for item in artifacts}
    selected_groups = set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS))
    target = _read_json(run_dir / "target-open" / "target-open-diagnostics.json")
    report = _read_json(run_dir / "analysis" / "facebook-sample-patterns.json")
    target_status = str((report.get("target_verification") or {}).get("status") or "")
    target_verified = bool(target.get("verified") or target_status == "matched")
    terminal_job = job_status in {"completed", "failed", "cancelled"}

    environment_state = "complete" if android.get("state") == "device" and android.get("boot_completed") else ("error" if terminal_job else "active")
    pcap_state = "complete" if "pcap" not in selected_groups or (ready.get("raw-network.pcap") and terminal_job) else ("active" if job_status == "running" else "waiting")
    proxy_state = "complete" if "mitm" not in selected_groups or (terminal_job and ready.get("raw-facebook-http.mitm")) else ("active" if job_status == "running" else "waiting")
    target_state = "complete" if target_verified else ("active" if job_status == "running" and ready.get("safe-facebook-http-capture.jsonl") else ("error" if terminal_job else "waiting"))
    reaction_state = "complete" if metrics["terminal"] else ("active" if metrics["reaction_users"] > 0 or job_status == "running" else ("error" if job_status == "failed" else "waiting"))
    decrypt_state = "complete" if "mitm" not in selected_groups or ready.get("raw-facebook-http-summary.json") else ("active" if ready.get("raw-facebook-http.mitm") and job_status == "running" else ("error" if job_status == "failed" else "waiting"))
    analysis_state = "complete" if ready.get("facebook-sample-patterns.json") else ("error" if job_status == "failed" else "waiting")
    selected_names = REQUIRED_ARTIFACTS | {
        name for group in selected_groups for name in OPTIONAL_ARTIFACT_GROUPS[group]
    }
    if job_status == "completed" and all(ready.get(name) for name in REQUIRED_ARTIFACTS):
        final_state = "complete" if all(ready.get(name) for name in selected_names) else "warning"
    elif job_status == "failed":
        final_state = "error"
    elif job_status == "cancelled":
        final_state = "warning"
    elif job_status == "running":
        final_state = "waiting"
    else:
        final_state = "waiting"

    stages = [
        _stage("environment", "模拟器与网络", environment_state, "ADB 设备和 Android 网络就绪"),
        _stage("pcap", "原始 PCAP", pcap_state, "tcpdump 保存 TLS 解密前流量" if "pcap" in selected_groups else "本次未选择保存原始 PCAP"),
        _stage("proxy", "代理与 MITM", proxy_state, "ADB reverse 与 mitmproxy 实时接收流量" if "mitm" in selected_groups else "MITM 仅用于处理，本次不保留证据文件"),
        _stage("target", "目标帖子校验", target_state, "只允许匹配目标帖的后续 Reaction 抓取"),
        _stage("pagination", "Reaction 分页", reaction_state, "ALL connection 持续增长直到 terminal"),
        _stage("decrypt", "解密 flow 校验", decrypt_state, "MITM archive 与安全索引完成校验" if "mitm" in selected_groups else "完成解密校验，本次不保留 MITM 证据文件"),
        _stage("analysis", "解析与去重", analysis_state, "GraphQL 节点标准化并输出 CSV"),
        _stage(
            "complete",
            "证据链完成",
            final_state,
            "PCAP、MITM、JSONL 和解析结果均已落盘"
            if selected_groups == set(DEFAULT_SAVE_ARTIFACTS)
            else "必留解析结果已落盘，可选证据按本次选择处理",
        ),
    ]
    return {
        "job_status": job_status,
        "storage_root": str(run_dir),
        "android": {
            "state": android.get("state", "unknown"),
            "boot_completed": bool(android.get("boot_completed")),
            "proxy": android.get("proxy", ""),
        },
        "metrics": metrics,
        "stages": stages,
        "artifacts": artifacts,
    }


def resolve_avatar_url(user_id: str, timeout_seconds: int = 15) -> str:
    """Resolve a fresh, signed Facebook CDN URL without downloading the image."""
    token = os.environ.get("FACEBOOK_GRAPH_ACCESS_TOKEN", "").strip()
    if not token:
        raise ApiError("FACEBOOK_GRAPH_ACCESS_TOKEN is not configured")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", user_id):
        raise ApiError("invalid user id")
    query = urlencode({"type": "large", "redirect": "false", "access_token": token})
    endpoint = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{user_id}/picture?{query}"
    try:
        request = Request(endpoint, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError("Facebook avatar URL resolution failed") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ApiError("Facebook did not return an avatar URL")
    if data.get("is_silhouette"):
        raise ApiError("Facebook returned a silhouette avatar")
    return url


def _enumeration_summary(report: dict[str, Any], family: str) -> dict[str, Any]:
    item = ((report.get("enumeration_status") or {}).get(family) or {})
    pagination = item.get("pagination") or {}
    groups = pagination.get("groups") or {}
    all_group = groups.get("__ALL__") or {}
    ui_total = item.get("ui_total_count")
    reported_total = item.get("reported_total_count")
    if reported_total is None:
        reported_total = ui_total
    enumerated = item.get("enumerated_identity_count")
    if enumerated is None:
        enumerated = 0
    gap = item.get("identity_count_gap")
    count_difference = gap is not None and gap > 0
    visibility_hint = item.get("ui_visibility_hint") or item.get("visibility_hint")
    limited = bool(count_difference or visibility_hint or str(item.get("status") or "").endswith("-subset"))
    count_difference_note = None
    if count_difference:
        count_difference_note = (
            "Facebook reported a larger interaction count than the identities returned by the "
            "current account's terminal visible list; treat this as a platform visibility/counting "
            "difference, not a capture failure."
        )
    return {
        "status": item.get("status"),
        "classification": item.get("classification"),
        "decision_reason": item.get("decision_reason"),
        "ui_total": ui_total,
        "reported_total": reported_total,
        "reported_total_source": item.get("reported_total_source"),
        "enumerated_count": enumerated,
        "identity_gap": gap,
        "count_difference_observed": count_difference,
        "limited": limited,
        "count_difference_note": count_difference_note,
        "visibility_hint": visibility_hint,
        "terminal": bool(
            pagination.get("all_observed_groups_terminal")
            or item.get("terminal_page_seen")
            or (family == "reactions" and all_group.get("terminal_page_seen"))
        ),
        "pagination": pagination,
    }


def _reaction_completeness(report: dict[str, Any]) -> dict[str, Any]:
    integrity = report.get("reaction_integrity") or {}
    item = ((report.get("enumeration_status") or {}).get("reactions") or {})
    pagination = item.get("pagination") or {}
    groups = pagination.get("groups") or {}
    all_group = groups.get("__ALL__") or {}
    terminal = bool(
        integrity.get("terminal_page_seen")
        or all_group.get("terminal_page_seen")
        or item.get("terminal_page_seen")
    )
    open_tail = bool(
        integrity.get("open_tail_without_terminal")
        or (all_group.get("open_page_seen") and not terminal)
    )
    completion_claim_allowed = (
        bool(integrity.get("completion_claim_allowed"))
        if "completion_claim_allowed" in integrity
        else bool(terminal and not open_tail)
    )
    integrity_status = integrity.get("integrity_status")
    if not integrity_status:
        integrity_status = (
            "complete-terminal"
            if completion_claim_allowed
            else "partial-open-tail"
            if open_tail
            else "incomplete"
        )
    return {
        "integrity_status": integrity_status,
        "completion_claim_allowed": completion_claim_allowed,
        "terminal_page_seen": terminal,
        "open_tail_without_terminal": open_tail,
        "successful_page_count": integrity.get("successful_page_count", len(report.get("reactor_pages") or [])),
        "raw_reactor_rows": integrity.get("raw_reactor_rows"),
        "unique_reactor_identities": integrity.get("unique_reactor_identities", item.get("enumerated_identity_count", 0)),
        "duplicate_reactor_rows": integrity.get("duplicate_reactor_rows", integrity.get("duplicate_rows")),
        "reported_total_count": integrity.get("reported_total_count", item.get("reported_total_count", item.get("ui_total_count"))),
        "reported_total_unique_gap": integrity.get("reported_total_unique_gap", item.get("identity_count_gap")),
        "rate_limited_records": integrity.get("rate_limited_records", 0),
        "temporary_feature_block_records": integrity.get("temporary_feature_block_records", 0),
    }


def build_api_result(run_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    analysis = run_dir / "analysis"
    report = _read_json(analysis / "facebook-sample-patterns.json")
    if not report:
        raise ApiError(f"analysis report missing or invalid: {analysis / 'facebook-sample-patterns.json'}")
    target = report.get("target_verification") or {}
    if (request.get("post_url") or request.get("post_id")) and target.get("status") != "matched":
        raise ApiError(f"target verification failed: {target.get('status')}")
    reactor_rows = _read_csv(analysis / "facebook-reactor-users.csv")
    share_rows = _read_csv(analysis / "facebook-share-users.csv")
    reaction_completeness = _reaction_completeness(report)
    if "reactions" in set(request.get("collect") or []):
        if not reaction_completeness.get("completion_claim_allowed"):
            raise ApiError(
                "reaction capture is incomplete: "
                f"{reaction_completeness.get('integrity_status') or 'unknown'}"
            )
        matched_post_id = str(target.get("matched_post_id") or "").strip()
        if not matched_post_id:
            raise ApiError("reaction capture lacks a verified matched_post_id")
        observed_sources = sorted(
            {
                str(value).strip()
                for value in ((report.get("source_post_ids_by_family") or {}).get("reactions") or [])
                if str(value).strip()
            }
        )
        if observed_sources != [matched_post_id]:
            raise ApiError(
                "reaction capture source mismatch: "
                f"expected={matched_post_id} observed={observed_sources}"
            )
        row_sources = {str(row.get("source_post_id") or "").strip() for row in reactor_rows}
        if row_sources and row_sources != {matched_post_id}:
            raise ApiError(
                "reaction user rows contain a different source_post_id: "
                f"expected={matched_post_id} observed={sorted(row_sources)}"
            )
        reported_total = reaction_completeness.get("reported_total_count")
        if reported_total not in (None, "") and int(reported_total) > 0 and not reactor_rows:
            raise ApiError("reaction capture completed without the visible reactor users reported by Facebook")
    reactions = [
        {
            "id": row.get("user_id") or None,
            "name": row.get("user_name") or None,
            "avatar_url": row.get("avatar_url") or None,
            # Facebook CDN URLs are useful source evidence but are not stable
            # public assets. Keep that distinction explicit in the API.
            "avatar_status": "refreshable" if os.environ.get("FACEBOOK_GRAPH_ACCESS_TOKEN", "").strip() else ("source-only" if row.get("avatar_url") else "unavailable"),
            "avatar_proxy_url": f"/v1/avatars/{row.get('user_id')}" if row.get("user_id") else None,
            "reaction_id": row.get("reaction_id") or None,
            "source_field": row.get("source_field") or None,
            "source_post_id": row.get("source_post_id") or None,
        }
        for row in reactor_rows
    ]
    shares = [
        {
            "id": row.get("actor_id") or None,
            "name": row.get("actor_name") or None,
            "source_post_id": row.get("source_post_id") or None,
            "share_story_id": row.get("share_story_id") or None,
        }
        for row in share_rows
    ]
    statuses = report.get("statuses") or {}
    return {
        "run_dir": str(run_dir),
        "target_verification": target,
        "completeness": {"reactions": reaction_completeness},
        "visibility": {
            "reactions": _enumeration_summary(report, "reactions"),
            "shares": _enumeration_summary(report, "shares"),
        },
        "reaction_users": reactions,
        "share_users": shares,
        "transport_observation": {
            "http_statuses": statuses,
            "rate_limit_http_seen": bool(statuses.get("429") or statuses.get(429)),
            "auth_http_error_seen": bool(statuses.get("401") or statuses.get(401) or statuses.get("403") or statuses.get(403)),
        },
    }


def _terminate_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.terminate()


def _api_run_dir(job_id: str) -> Path:
    return BASE_DIR / "runs" / f"api-{job_id}"


def _prune_optional_artifacts(run_dir: Path, request: dict[str, Any]) -> None:
    selected_groups = set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS))
    for group, names in OPTIONAL_ARTIFACT_GROUPS.items():
        if group in selected_groups:
            continue
        for name in names:
            path = run_dir / ARTIFACT_PATHS[name]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure must not hide an otherwise usable final result.
                pass


def run_capture_job(
    request: dict[str, Any],
    job_id: str,
    serial: str = DEFAULT_SERIAL,
    timeout_seconds: int | None = None,
    process_callback: Callable[[subprocess.Popen[Any] | None], None] | None = None,
    run_dir: Path | None = None,
    worker: WorkerConfig | None = None,
) -> dict[str, Any]:
    worker = worker or _default_worker(serial)
    serial = worker.serial
    run_dir = run_dir or _api_run_dir(job_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    ensure_emulator(worker=worker)
    command = build_capture_command(
        request,
        serial,
        run_dir,
        proxy_port=worker.capture_port,
        native_port=worker.native_port,
        frida_ssl_unpin=worker.frida_ssl_unpin,
    )
    job_dir = BASE_DIR / "api-jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / f"{job_id}.log"
    kwargs: dict[str, Any] = {}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation:
        kwargs["creationflags"] = creation
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
            if process_callback is not None:
                process_callback(process)
            try:
                if timeout_seconds is None or timeout_seconds <= 0:
                    process.wait()
                else:
                    try:
                        process.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired as exc:
                        _terminate_tree(process)
                        raise ApiError(f"capture timed out after {timeout_seconds}s") from exc
            finally:
                if process_callback is not None:
                    process_callback(None)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if process.returncode != 0:
            tail = "\n".join(log_text.splitlines()[-20:])
            raise ApiError(f"capture command failed with exit {process.returncode}; run_dir={run_dir}; tail={tail[-3000:]}")
        result = build_api_result(run_dir, request)
        result["job_log"] = str(log_path)
        return result
    finally:
        _prune_optional_artifacts(run_dir, request)


class JobManager:
    def __init__(
        self,
        serial: str = DEFAULT_SERIAL,
        *,
        workers: list[WorkerConfig] | None = None,
        worker_config_path: Path | None = None,
    ):
        self.worker_config_path = worker_config_path or (BASE_DIR / WORKER_CONFIG_NAME)
        configured = validate_worker_config(list(workers)) if workers is not None else load_worker_config(
            self.worker_config_path, fallback_serial=serial
        )
        self.workers: dict[str, WorkerConfig] = {worker.worker_id: worker for worker in configured}
        self.serial = configured[0].serial
        self._queue: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._running_processes: dict[str, subprocess.Popen[Any]] = {}
        self._worker_runtime: dict[str, dict[str, Any]] = {
            worker.worker_id: {"current_job_id": None, "cooldown_until": 0.0}
            for worker in configured
        }
        self._workers: list[threading.Thread] = []
        self._started_worker_ids: set[str] = set()
        self._lock = threading.RLock()
        self._load_persisted_jobs()
        self._load_persisted_batches()
        self._worker: threading.Thread | None = None
        for worker in configured:
            if worker.enabled:
                thread = self._start_worker(worker.worker_id)
                if self._worker is None:
                    self._worker = thread

    def _start_worker(self, worker_id: str) -> threading.Thread:
        with self._lock:
            if worker_id in self._started_worker_ids:
                return next(thread for thread in self._workers if thread.name.endswith(worker_id))
            if worker_id not in self.workers:
                raise KeyError(worker_id)
            thread = threading.Thread(
                target=self._worker_loop,
                args=(worker_id,),
                name=f"facebook-capture-{worker_id}",
                daemon=True,
            )
            self._workers.append(thread)
            self._started_worker_ids.add(worker_id)
        thread.start()
        return thread

    @staticmethod
    def _manifest_payload(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "request": job.get("request"),
            "error": job.get("error"),
            "run_dir": job.get("run_dir"),
            "batch_id": job.get("batch_id"),
            "batch_index": job.get("batch_index"),
            "worker_id": job.get("worker_id"),
            "serial": job.get("serial"),
        }

    @staticmethod
    def _batch_manifest_payload(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "batch_id": batch.get("batch_id"),
            "created_at": batch.get("created_at"),
            "job_ids": list(batch.get("job_ids") or []),
            "run_dir": batch.get("run_dir"),
        }

    def _persist_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ApiError("cannot persist job without job_id")
        _atomic_write_json(_job_manifest_path(job_id), self._manifest_payload(job))

    def _persist_batch(self, batch: dict[str, Any]) -> None:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            raise ApiError("cannot persist batch without batch_id")
        _atomic_write_json(_batch_manifest_path(batch_id), self._batch_manifest_payload(batch))

    def _load_persisted_jobs(self) -> None:
        job_dir = BASE_DIR / "api-jobs"
        if not job_dir.exists():
            return
        allowed_statuses = {"queued", "running", "completed", "failed", "cancelled"}
        for path in sorted(job_dir.glob("fb_*.job.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(raw, dict) or raw.get("version") != 1:
                    continue
                job_id = str(raw.get("job_id") or "").strip()
                expected_job_id = path.name.removesuffix(".job.json")
                if not job_id or job_id != expected_job_id:
                    continue
                status = str(raw.get("status") or "").strip()
                if status not in allowed_statuses:
                    continue
                request = normalize_request(raw.get("request") or {})
                job = {
                    "job_id": job_id,
                    "status": status,
                    "created_at": raw.get("created_at") or utc_now(),
                    "started_at": raw.get("started_at"),
                    "finished_at": raw.get("finished_at"),
                    "request": request,
                    "result": None,
                    "error": raw.get("error"),
                    "run_dir": raw.get("run_dir") or None,
                    "batch_id": raw.get("batch_id") or None,
                    "batch_index": raw.get("batch_index"),
                    "worker_id": raw.get("worker_id") or None,
                    "serial": raw.get("serial") or None,
                }
                if status in {"queued", "running"}:
                    job["status"] = "failed"
                    job["error"] = "interrupted by API service restart; capture was not automatically resumed"
                    job["finished_at"] = utc_now()
                    self._persist_job(job)
                self._jobs[job_id] = job
            except (OSError, json.JSONDecodeError, ApiError, TypeError, ValueError):
                # Ignore malformed/stale manifests rather than preventing API startup.
                continue

    def _load_persisted_batches(self) -> None:
        batch_dir = BASE_DIR / "api-batches"
        if not batch_dir.exists():
            return
        for path in sorted(batch_dir.glob("batch_*.batch.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                batch_id = str(raw.get("batch_id") or "").strip()
                job_ids = [str(value) for value in raw.get("job_ids") or []]
                if raw.get("version") != 1 or path.name != f"{batch_id}.batch.json":
                    continue
                if not job_ids or any(job_id not in self._jobs for job_id in job_ids):
                    continue
                self._batches[batch_id] = {
                    "batch_id": batch_id,
                    "created_at": raw.get("created_at") or utc_now(),
                    "job_ids": job_ids,
                    "run_dir": raw.get("run_dir") or str(BASE_DIR / "runs" / batch_id),
                }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(payload)
        job_id = f"fb_{uuid.uuid4().hex[:16]}"
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "request": request,
            "result": None,
            "error": None,
            "run_dir": None,
            "batch_id": None,
            "batch_index": None,
            "worker_id": None,
            "serial": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist_job(job)
        self._queue.put(job_id)
        return self.public_job(job_id)

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_batch_request(payload)
        batch_id = f"batch_{uuid.uuid4().hex[:16]}"
        batch_run_dir = BASE_DIR / "runs" / batch_id
        created_at = utc_now()
        jobs: list[dict[str, Any]] = []
        for index, post_url in enumerate(request["post_urls"], start=1):
            job_id = f"fb_{uuid.uuid4().hex[:16]}"
            jobs.append(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "created_at": created_at,
                    "started_at": None,
                    "finished_at": None,
                    "request": {
                        "post_url": post_url,
                        "post_id": "",
                        "collect": list(request["collect"]),
                        "save_artifacts": list(request["save_artifacts"]),
                    },
                    "result": None,
                    "error": None,
                    "run_dir": str(batch_run_dir / f"post-{index:03d}"),
                    "batch_id": batch_id,
                    "batch_index": index,
                    "worker_id": None,
                    "serial": None,
                }
            )
        batch = {
            "batch_id": batch_id,
            "created_at": created_at,
            "job_ids": [job["job_id"] for job in jobs],
            "run_dir": str(batch_run_dir),
        }
        with self._lock:
            self._batches[batch_id] = batch
            for job in jobs:
                self._jobs[job["job_id"]] = job
                self._persist_job(job)
            self._persist_batch(batch)
        write_batch_outputs(batch_run_dir, batch_id, jobs)
        for job in jobs:
            self._queue.put(job["job_id"])
        return self.batch_progress(batch_id)

    def _batch_jobs(self, batch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if not batch:
                raise KeyError(batch_id)
            batch_copy = dict(batch)
            jobs = [dict(self._jobs[job_id]) for job_id in batch["job_ids"] if job_id in self._jobs]
        return batch_copy, jobs

    def _refresh_batch_outputs(self, batch_id: str) -> dict[str, Any]:
        batch, jobs = self._batch_jobs(batch_id)
        return write_batch_outputs(Path(batch["run_dir"]), batch_id, jobs)

    def batch_progress(self, batch_id: str) -> dict[str, Any]:
        batch, jobs = self._batch_jobs(batch_id)
        batch_dir = Path(batch["run_dir"])
        summary = _read_json(batch_dir / "batch-summary.json")
        if not summary:
            summary = write_batch_outputs(batch_dir, batch_id, jobs)
        post_summaries: list[dict[str, Any]] = []
        for index, job in enumerate(jobs, start=1):
            status = str(job.get("status") or "queued")
            run_dir = Path(str(job.get("run_dir") or ""))
            metrics = _reaction_progress(run_dir) if str(job.get("run_dir") or "") else {}
            valid_users = _valid_reactor_user_count(run_dir) if str(job.get("run_dir") or "") else 0
            if status == "running":
                snapshot = build_progress_snapshot(run_dir, status, request=job.get("request") or {})
                active = next((stage["label"] for stage in snapshot["stages"] if stage["state"] == "active"), "抓取中")
            elif status == "completed":
                active = "证据链完成"
            elif status == "failed":
                if metrics.get("stop_reason") == "rate-limited" and int(metrics.get("reaction_users") or 0) > 0:
                    active = "限流，已保留部分结果"
                else:
                    active = "失败"
            elif status == "cancelled":
                active = "已停止"
            else:
                active = "等待队列"
            post_summaries.append(
                {
                    "index": int(job.get("batch_index") or index),
                    "job_id": job["job_id"],
                    "post_url": str((job.get("request") or {}).get("post_url") or ""),
                    "status": status,
                    "current_stage": active,
                    "valid_users": valid_users or int(metrics.get("reaction_users") or 0),
                    "reaction_pages": int(metrics.get("reaction_pages") or 0),
                    "run_dir": str(job.get("run_dir") or ""),
                    "worker_id": job.get("worker_id"),
                    "serial": job.get("serial"),
                }
            )
        status_counts = {name: sum(1 for job in jobs if job.get("status") == name) for name in ("queued", "running", "completed", "failed", "cancelled")}
        terminal_count = status_counts["completed"] + status_counts["failed"] + status_counts["cancelled"]
        if status_counts["running"]:
            live_status = "running"
        elif status_counts["queued"]:
            live_status = "queued"
        elif terminal_count == len(jobs) and (status_counts["failed"] or status_counts["cancelled"]):
            live_status = "completed_with_errors"
        elif terminal_count == len(jobs):
            live_status = "completed"
        else:
            live_status = "queued"
        summary.update(
            {
                "status": live_status,
                "total_posts": len(jobs),
                "status_counts": status_counts,
                "per_post_valid_sum": sum(int(post["valid_users"]) for post in post_summaries),
            }
        )
        artifacts: list[dict[str, Any]] = []
        for name, label, layer in BATCH_ARTIFACT_SPECS:
            path = batch_dir / name
            try:
                size = path.stat().st_size if path.is_file() else 0
            except OSError:
                size = 0
            artifacts.append(
                {
                    "name": name,
                    "label": label,
                    "layer": layer,
                    "path": str(path),
                    "size_bytes": size,
                    "state": "ready" if size else "waiting",
                    "viewable": True,
                    "url": f"/v1/batches/{batch_id}/artifacts/{name}",
                }
            )
        return {
            **summary,
            "created_at": batch.get("created_at"),
            "run_dir": batch["run_dir"],
            "posts": post_summaries,
            "artifacts": artifacts,
        }

    def latest_batch(self) -> dict[str, Any]:
        with self._lock:
            if not self._batches:
                raise KeyError("latest")
            batch_id = max(self._batches, key=lambda value: str(self._batches[value].get("created_at") or ""))
        return self.batch_progress(batch_id)

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        _batch, jobs = self._batch_jobs(batch_id)
        cancelled = 0
        for job in jobs:
            response = self.cancel(job["job_id"])
            cancelled += int(bool(response["cancelled"]))
        self._refresh_batch_outputs(batch_id)
        return {"cancelled_jobs": cancelled, "batch": self.batch_progress(batch_id)}

    def batch_artifact(self, batch_id: str, artifact_name: str) -> Path:
        allowed = {name for name, _label, _layer in BATCH_ARTIFACT_SPECS}
        if artifact_name not in allowed:
            raise ApiError("unknown batch artifact")
        batch, _jobs = self._batch_jobs(batch_id)
        path = Path(batch["run_dir"]) / artifact_name
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                raise ApiError("batch artifact is not available yet")
        except OSError as exc:
            raise ApiError("batch artifact is not available yet") from exc
        return path

    def _set_running_process(self, job_id: str, process: subprocess.Popen[Any] | None) -> None:
        terminate_now = False
        with self._lock:
            job = self._jobs.get(job_id)
            if process is None:
                self._running_processes.pop(job_id, None)
                return
            if job and job.get("status") == "cancelled":
                terminate_now = True
            else:
                self._running_processes[job_id] = process
        if terminate_now:
            _terminate_tree(process)

    def cancel(self, job_id: str) -> dict[str, Any]:
        process: subprocess.Popen[Any] | None = None
        cancelled = False
        reason = ""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            status = str(job.get("status") or "")
            if status == "queued":
                job["status"] = "cancelled"
                job["finished_at"] = utc_now()
                job["error"] = None
                self._persist_job(job)
                cancelled = True
                reason = "queued job cancelled"
            elif status == "running":
                job["status"] = "cancelled"
                job["finished_at"] = utc_now()
                job["error"] = None
                self._persist_job(job)
                process = self._running_processes.get(job_id)
                cancelled = True
                reason = "running job cancellation requested"
            elif status == "cancelled":
                reason = "job is already cancelled"
            else:
                reason = f"job is already {status}"
        if process is not None:
            _terminate_tree(process)
        return {
            "cancelled": cancelled,
            "reason": reason,
            "job": self.public_job(job_id),
        }

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            payload = {k: v for k, v in job.items() if k != "result"}
        run_dir = str(payload.get("run_dir") or "").strip()
        partial_evidence_available = False
        if run_dir:
            run_path = Path(run_dir)
            if not run_path.is_absolute():
                run_path = BASE_DIR / run_path
            capture = run_path / "safe-facebook-http-capture.jsonl"
            try:
                partial_evidence_available = capture.exists() and capture.stat().st_size > 0
            except OSError:
                partial_evidence_available = False
        payload["partial_evidence_available"] = partial_evidence_available
        return payload

    def latest_job(self) -> dict[str, Any]:
        with self._lock:
            if not self._jobs:
                raise KeyError("latest")
            job_id = max(
                self._jobs,
                key=lambda value: str(self._jobs[value].get("created_at") or ""),
            )
        return self.public_job(job_id)

    def result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job["status"] != "completed":
                raise ApiError(f"job is {job['status']}")
            if job.get("result") is not None:
                return job["result"]
            run_dir = str(job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
        if not run_dir:
            raise ApiError("completed job is missing persisted run_dir")
        run_path = Path(run_dir)
        if not run_path.is_absolute():
            run_path = BASE_DIR / run_path
        if not run_path.exists():
            raise ApiError(f"completed job run directory is missing: {run_path}")
        result = build_api_result(run_path, request)
        with self._lock:
            current = self._jobs.get(job_id)
            if current and current.get("status") == "completed":
                current["result"] = result
        return result

    def artifact(self, job_id: str, artifact_name: str) -> Path:
        relative = ARTIFACT_PATHS.get(artifact_name)
        if relative is None:
            raise ApiError("unknown artifact")
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            run_dir = str(job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
        group = ARTIFACT_GROUP_BY_NAME.get(artifact_name)
        if group and group not in set(request.get("save_artifacts", DEFAULT_SAVE_ARTIFACTS)):
            raise ApiError("artifact was not selected for saving")
        if not run_dir:
            raise ApiError("job has no artifact directory yet")
        run_path = Path(run_dir)
        if not run_path.is_absolute():
            run_path = BASE_DIR / run_path
        artifact_path = run_path / relative
        try:
            if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
                raise ApiError("artifact is not available yet")
        except OSError as exc:
            raise ApiError("artifact is not available yet") from exc
        return artifact_path

    def _assign_job_to_worker(self, job_id: str, worker: WorkerConfig) -> bool:
        with self._lock:
            current = self.workers.get(worker.worker_id)
            job = self._jobs.get(job_id)
            runtime = self._worker_runtime.get(worker.worker_id)
            if not current or not current.enabled or not job or not runtime:
                return False
            if job.get("status") != "queued" or float(runtime.get("cooldown_until") or 0) > time.time():
                return False
            job["status"] = "running"
            job["started_at"] = utc_now()
            job["run_dir"] = str(job.get("run_dir") or _api_run_dir(job_id))
            job["worker_id"] = current.worker_id
            job["serial"] = current.serial
            runtime["current_job_id"] = job_id
            self._persist_job(job)
            return True

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            current = self.workers.get(worker_id)
            if not current:
                raise KeyError(worker_id)
            updated = replace(current, enabled=enabled)
            candidate = [updated if worker.worker_id == worker_id else worker for worker in self.workers.values()]
            validate_worker_config(candidate)
            self.workers = {worker.worker_id: worker for worker in candidate}
            write_worker_config(self.worker_config_path, candidate)
        if enabled:
            self._start_worker(worker_id)
        return self.worker_health(worker_id)

    def reload_workers(self) -> dict[str, Any]:
        configured = load_worker_config(self.worker_config_path, fallback_serial=self.serial)
        configured_by_id = {worker.worker_id: worker for worker in configured}
        start_worker_ids: list[str] = []
        with self._lock:
            current_ids = list(self.workers)
            configured_ids = [worker.worker_id for worker in configured]
            removed = [worker_id for worker_id in current_ids if worker_id not in configured_by_id]
            if removed:
                raise ApiError(f"worker reload cannot remove loaded workers: {', '.join(removed)}")
            if configured_ids[: len(current_ids)] != current_ids:
                raise ApiError("worker reload cannot reorder loaded workers; new workers must be appended")

            updated_ids: list[str] = []
            for worker_id in current_ids:
                current = self.workers[worker_id]
                replacement = configured_by_id[worker_id]
                current_identity = (
                    current.serial,
                    current.avd_name,
                    current.capture_port,
                    current.native_port,
                )
                replacement_identity = (
                    replacement.serial,
                    replacement.avd_name,
                    replacement.capture_port,
                    replacement.native_port,
                )
                if current_identity != replacement_identity:
                    raise ApiError(f"worker reload cannot change immutable identity for {worker_id}")
                if replacement != current:
                    updated_ids.append(worker_id)
                    if replacement.enabled and not current.enabled:
                        start_worker_ids.append(worker_id)

            added = [worker for worker in configured if worker.worker_id not in self.workers]
            enabled_additions = [worker.worker_id for worker in added if worker.enabled]
            if enabled_additions:
                raise ApiError(
                    "new workers must be loaded disabled before explicit enable: "
                    + ", ".join(enabled_additions)
                )

            self.workers = dict(configured_by_id)
            for worker in added:
                self._worker_runtime[worker.worker_id] = {
                    "current_job_id": None,
                    "cooldown_until": 0.0,
                }
            self.serial = configured[0].serial

        for worker_id in start_worker_ids:
            self._start_worker(worker_id)
        return {
            "added": [worker.worker_id for worker in added],
            "updated": updated_ids,
            "worker_ids": [worker.worker_id for worker in configured],
        }

    def worker_health(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            runtime = dict(self._worker_runtime[worker_id])
        snapshot = adb_snapshot(worker.serial)
        now = time.time()
        cooldown_until = float(runtime.get("cooldown_until") or 0)
        if not worker.enabled:
            state = "disabled"
        elif runtime.get("current_job_id"):
            state = "busy"
        elif cooldown_until > now:
            state = "cooling"
        elif snapshot.get("state") != "device" or not snapshot.get("boot_completed"):
            state = "offline"
        else:
            state = "idle"
        return {
            "worker_id": worker.worker_id,
            "serial": worker.serial,
            "avd_name": worker.avd_name,
            "capture_port": worker.capture_port,
            "native_port": worker.native_port,
            "enabled": worker.enabled,
            "state": state,
            "current_job_id": runtime.get("current_job_id"),
            "cooldown_until": (
                datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat().replace("+00:00", "Z")
                if cooldown_until > now
                else None
            ),
            "android": snapshot,
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for job in self._jobs.values() if job["status"] == "queued")
            running = sum(1 for job in self._jobs.values() if job["status"] == "running")
            worker_ids = list(self.workers)
        worker_health = [self.worker_health(worker_id) for worker_id in worker_ids]
        primary = worker_health[0]
        return {
            "service": "ready",
            "worker": "busy" if running else "idle",
            "queued_jobs": queued,
            "android": primary["android"],
            "serial": self.serial,
            "workers": worker_health,
        }

    def progress(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            status = str(job.get("status") or "queued")
            run_dir = str(job.get("run_dir") or "").strip()
            request = dict(job.get("request") or {})
            serial = str(job.get("serial") or self.serial)
        run_path = Path(run_dir) if run_dir else _api_run_dir(job_id)
        if not run_path.is_absolute():
            run_path = BASE_DIR / run_path
        return build_progress_snapshot(run_path, status, adb_snapshot(serial), request)

    def _worker_loop(self, worker_id: str) -> None:
        while True:
            with self._lock:
                worker = self.workers[worker_id]
                runtime = self._worker_runtime[worker_id]
                available = worker.enabled and float(runtime.get("cooldown_until") or 0) <= time.time()
            if not available:
                time.sleep(0.5)
                continue
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if not self._assign_job_to_worker(job_id, worker):
                    with self._lock:
                        if (self._jobs.get(job_id) or {}).get("status") == "queued":
                            self._queue.put(job_id)
                    time.sleep(0.1)
                    continue
                with self._lock:
                    job = self._jobs[job_id]
                    request = dict(job["request"])
                    run_dir = Path(job["run_dir"])
                result = run_capture_job(
                    request,
                    job_id,
                    worker.serial,
                    process_callback=lambda process: self._set_running_process(job_id, process),
                    run_dir=run_dir,
                    worker=worker,
                )
                with self._lock:
                    job = self._jobs[job_id]
                    if job.get("status") == "cancelled":
                        continue
                    job["status"] = "completed"
                    job["result"] = result
                    job["run_dir"] = result.get("run_dir")
                    job["finished_at"] = utc_now()
                    self._persist_job(job)
            except Exception as exc:  # worker must remain available for the next job
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job and job.get("status") != "cancelled":
                        job["status"] = "failed"
                        job["error"] = str(exc)
                        job["finished_at"] = utc_now()
                        self._persist_job(job)
                    if "rate-limit" in str(exc).lower():
                        self._worker_runtime[worker_id]["cooldown_until"] = time.time() + worker.cooldown_seconds
            finally:
                self._set_running_process(job_id, None)
                with self._lock:
                    batch_id = str((self._jobs.get(job_id) or {}).get("batch_id") or "")
                    self._worker_runtime[worker_id]["current_job_id"] = None
                if batch_id:
                    try:
                        self._refresh_batch_outputs(batch_id)
                    except (OSError, ApiError, KeyError):
                        pass
                self._queue.task_done()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "FacebookCaptureAPI/0.1"

    @property
    def manager(self) -> JobManager:
        return self.server.manager  # type: ignore[attr-defined]

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._json(404, {"error": "asset not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _artifact(self, path: Path, download: bool) -> None:
        content_type = "text/csv; charset=utf-8" if path.suffix.lower() == ".csv" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        disposition = "attachment" if download or path.suffix.lower() not in INLINE_ARTIFACT_SUFFIXES else "inline"
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self._json(404, {"error": "artifact not found"})
            return
        with handle:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'{disposition}; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            while chunk := handle.read(1024 * 1024):
                self.wfile.write(chunk)

    def _segments(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def do_GET(self) -> None:
        segments = self._segments()
        if segments == ["guide"]:
            body = (Path(__file__).resolve().parent / "README.md").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        static = resolve_static_asset(self.path)
        if static is not None:
            self._static(static)
            return
        if segments == ["health"]:
            self._json(200, self.manager.health())
            return
        if segments == ["v1", "jobs", "latest"]:
            try:
                self._json(200, self.manager.latest_job())
            except KeyError:
                self._json(404, {"error": "no jobs found"})
            return
        if segments == ["v1", "batches", "latest"]:
            try:
                self._json(200, self.manager.latest_batch())
            except KeyError:
                self._json(404, {"error": "no batches found"})
            return
        if len(segments) == 5 and segments[:2] == ["v1", "batches"] and segments[3] == "artifacts":
            try:
                artifact = self.manager.batch_artifact(segments[2], segments[4])
                download = urlparse(self.path).query == "download=1"
                self._artifact(artifact, download)
            except KeyError:
                self._json(404, {"error": "batch not found"})
            except ApiError as exc:
                self._json(404, {"error": str(exc)})
            return
        if len(segments) in {3, 4} and segments[:2] == ["v1", "batches"]:
            try:
                if len(segments) == 4 and segments[3] == "progress":
                    self._json(200, self.manager.batch_progress(segments[2]))
                elif len(segments) == 3:
                    self._json(200, self.manager.batch_progress(segments[2]))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError:
                self._json(404, {"error": "batch not found"})
            return
        if len(segments) == 3 and segments[:2] == ["v1", "avatars"]:
            try:
                avatar_url = resolve_avatar_url(segments[2])
                self.send_response(302)
                self.send_header("Location", avatar_url)
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
            except ApiError as exc:
                self._json(503, {"error": str(exc)})
            return
        if len(segments) == 5 and segments[:2] == ["v1", "jobs"] and segments[3] == "artifacts":
            try:
                artifact = self.manager.artifact(segments[2], segments[4])
                download = urlparse(self.path).query == "download=1"
                self._artifact(artifact, download)
            except KeyError:
                self._json(404, {"error": "job not found"})
            except ApiError as exc:
                self._json(404, {"error": str(exc)})
            return
        if len(segments) in {3, 4} and segments[:2] == ["v1", "jobs"]:
            job_id = segments[2]
            try:
                if len(segments) == 4 and segments[3] == "result":
                    self._json(200, self.manager.result(job_id))
                elif len(segments) == 4 and segments[3] == "progress":
                    self._json(200, self.manager.progress(job_id))
                elif len(segments) == 3:
                    self._json(200, self.manager.public_job(job_id))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError:
                self._json(404, {"error": "job not found"})
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        segments = self._segments()
        if segments == ["v1", "workers", "reload"]:
            try:
                self._json(200, self.manager.reload_workers())
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            return
        if len(segments) == 4 and segments[:2] == ["v1", "workers"] and segments[3] in {"enable", "disable"}:
            try:
                self._json(200, self.manager.set_worker_enabled(segments[2], segments[3] == "enable"))
            except KeyError:
                self._json(404, {"error": "worker not found"})
            except ApiError as exc:
                self._json(409, {"error": str(exc)})
            return
        if len(segments) == 4 and segments[:2] == ["v1", "batches"] and segments[3] == "cancel":
            try:
                self._json(200, self.manager.cancel_batch(segments[2]))
            except KeyError:
                self._json(404, {"error": "batch not found"})
            return
        if len(segments) == 4 and segments[:2] == ["v1", "jobs"] and segments[3] == "cancel":
            try:
                self._json(200, self.manager.cancel(segments[2]))
            except KeyError:
                self._json(404, {"error": "job not found"})
            return
        if segments not in (["v1", "jobs"], ["v1", "batches"]):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ApiError("invalid request body size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.manager.submit_batch(payload) if segments == ["v1", "batches"] else self.manager.submit(payload)
            self._json(202, result)
        except (ApiError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} {fmt % args}")


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], manager: JobManager):
        super().__init__(address, ApiHandler)
        self.manager = manager


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Facebook Android capture API")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    args = parser.parse_args()
    if (BASE_DIR / 'facebook.local.json').is_file():
        from facebook_local import configuration, environment
        environment(configuration(BASE_DIR), BASE_DIR)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing non-loopback bind; expose this service only through an authenticated private tunnel")
    manager = JobManager(args.serial)
    server = CaptureServer((args.host, args.port), manager)
    print(f"Facebook Capture API ready: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
