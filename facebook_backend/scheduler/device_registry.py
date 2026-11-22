"""Worker / device registry.

One worker owns exactly one Android device: a serial, an AVD name and a pair of
ports (mitmproxy capture port + native port).  The configuration file
(``facebook_workers.json``) stays the source of truth — the task explicitly
forbids managing the same thing in both a database and a JSON file.

The validation rules are moved verbatim from ``facebook_capture_api``:

* at least one worker, at least one enabled;
* ``worker_id`` / ``serial`` / ``avd_name`` unique;
* ``worker_id`` matches ``[A-Za-z0-9][A-Za-z0-9_.-]{0,63}``;
* serial and avd_name non-blank;
* both ports in 1024..65535, different from each other, globally unique;
* non-negative cooldown.

Phase 4 turns this registry into the device-aware scheduler's inventory; the
rules and the file format must not change in the process.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from facebook_backend.domain.exceptions import ApiError

DEFAULT_SERIAL = "emulator-5554"
DEFAULT_AVD_NAME = "fb-x86_64-api35"
DEFAULT_CAPTURE_PORT = 8081
DEFAULT_NATIVE_PORT = 18766
WORKER_CONFIG_NAME = "facebook_workers.json"
DEFAULT_COOLDOWN_SECONDS = 900


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    frida_ssl_unpin: bool = False


def default_worker(serial: str = DEFAULT_SERIAL) -> WorkerConfig:
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


def load_worker_config(
    path: Path | None = None,
    fallback_serial: str = DEFAULT_SERIAL,
    *,
    base_dir: Path,
) -> list[WorkerConfig]:
    config_path = path or (base_dir / WORKER_CONFIG_NAME)
    if not config_path.is_file():
        return [default_worker(fallback_serial)]
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
                    cooldown_seconds=int(raw.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
                    frida_ssl_unpin=frida_ssl_unpin,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("worker configuration contains an invalid worker entry") from exc
    return validate_worker_config(workers)


def write_worker_config(
    path: Path,
    workers: list[WorkerConfig],
    *,
    writer: Callable[[Path, object], None],
) -> None:
    validated = validate_worker_config(workers)
    writer(path, {"version": 1, "workers": [asdict(worker) for worker in validated]})


__all__ = [
    "DEFAULT_AVD_NAME",
    "DEFAULT_CAPTURE_PORT",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_NATIVE_PORT",
    "DEFAULT_SERIAL",
    "WORKER_CONFIG_NAME",
    "WorkerConfig",
    "default_worker",
    "load_worker_config",
    "validate_worker_config",
    "write_worker_config",
]
