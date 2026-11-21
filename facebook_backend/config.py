"""Runtime settings for the FastAPI backend.

Values are read from environment variables so the same code runs under the
legacy ``python facebook_capture_api.py`` entrypoint and the new
``python -m facebook_backend`` entrypoint.  Nothing here touches the network,
the filesystem or the scheduler: construction is pure.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_SERIAL = "emulator-5554"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - misconfiguration guard
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable backend configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    serial: str = DEFAULT_SERIAL
    base_dir: Path = PROJECT_ROOT
    web_dir: Path = PROJECT_ROOT / "web"
    max_request_bytes: int = 64 * 1024
    request_timeout_seconds: int = 15
    load_local_config: bool = True
    #: Hard ceiling for one capture run (0 = unlimited, the historical behaviour).
    max_runtime_seconds: int = 6 * 60 * 60
    #: How long the service waits for the running job during shutdown.  Must stay
    #: well below ``max_runtime_seconds``: a shutdown wait longer than the job
    #: budget would mean the process is killed by the supervisor anyway.
    shutdown_wait_seconds: float = 30.0
    log_level: str = "INFO"
    #: Where job/batch state lives.  ``manifest`` keeps the historical JSON
    #: files; ``database`` makes SQLite the authoritative store (requires a
    #: migrated schema and an offline manifest import first).
    job_store: str = "manifest"
    #: Only used when ``job_store == "database"``.
    database_url: str | None = None
    # The in-process JobManager owns a queue, a scheduler thread and the
    # capture lease.  Running more than one Uvicorn worker would create
    # independent schedulers over the same manifests, so it stays pinned at 1
    # until cross-process coordination exists.
    uvicorn_workers: int = 1

    @property
    def worker_config_path(self) -> Path:
        return self.base_dir / "facebook_workers.json"

    @property
    def local_config_path(self) -> Path:
        return self.base_dir / "facebook.local.json"

    def with_overrides(self, **changes) -> "Settings":
        return replace(self, **changes)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        settings = cls(
            host=os.environ.get("FACEBOOK_API_HOST", DEFAULT_HOST),
            port=_int_env("FACEBOOK_API_PORT", DEFAULT_PORT),
            serial=os.environ.get("FACEBOOK_CAPTURE_SERIAL", DEFAULT_SERIAL),
            max_request_bytes=_int_env("FACEBOOK_API_MAX_REQUEST_BYTES", 64 * 1024),
            request_timeout_seconds=_int_env("FACEBOOK_API_REQUEST_TIMEOUT", 15),
            load_local_config=os.environ.get("FACEBOOK_API_LOAD_LOCAL_CONFIG", "1") != "0",
            max_runtime_seconds=_int_env(
                "FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS", 6 * 60 * 60
            ),
            shutdown_wait_seconds=float(
                os.environ.get("FACEBOOK_SHUTDOWN_WAIT_SECONDS", "30")
            ),
            log_level=os.environ.get("FACEBOOK_LOG_LEVEL", "INFO"),
            job_store=os.environ.get("FACEBOOK_JOB_STORE", "manifest"),
            database_url=os.environ.get("FACEBOOK_DB_URL") or None,
            uvicorn_workers=_int_env("FACEBOOK_API_WORKERS", 1),
        )
        return settings.with_overrides(**overrides) if overrides else settings
