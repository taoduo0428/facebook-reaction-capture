"""Control-plane entrypoint for the container image.

The image deliberately contains no ADB, no Android SDK and no emulator, so it
must never own real execution: a recovered capture process cannot be verified
from inside a container that cannot see the host's device.  This module therefore
serves the read-only control surface (health, metrics, job/batch reads) and
refuses submissions with an explicit 503 instead of silently starting a second
state owner next to the native service.

Native Windows/macOS installs keep using ``python -m facebook_backend``.
"""
from __future__ import annotations

from typing import Any

from facebook_backend.config import Settings
from facebook_backend.main import create_app

CONTROL_PLANE_MESSAGE = (
    "this container is a control plane without a device; run the capture executor "
    "natively (python -m facebook_backend) to accept jobs"
)


class ControlPlaneManager:
    """Read-only facade: no queue, no scheduler thread, no subprocesses."""

    accepting_submissions = False
    submission_refusal = CONTROL_PLANE_MESSAGE

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}

    # -- reads -----------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return {
            "service": "ready",
            "mode": "control-plane",
            "scheduler": {"state": "disabled", "alive": False},
            "worker": "disabled",
            "queued_jobs": 0,
            "cancelling_jobs": 0,
            "android": {"state": "unavailable", "boot_completed": False, "proxy": ""},
            "workers": [],
            "note": CONTROL_PLANE_MESSAGE,
        }

    def latest_job(self) -> dict[str, Any]:
        raise KeyError("latest")

    def public_job(self, job_id: str) -> dict[str, Any]:
        raise KeyError(job_id)

    def progress(self, job_id: str) -> dict[str, Any]:
        raise KeyError(job_id)

    def result(self, job_id: str) -> dict[str, Any]:
        raise KeyError(job_id)

    def artifact(self, job_id: str, artifact_name: str):
        raise KeyError(job_id)

    def latest_batch(self) -> dict[str, Any]:
        raise KeyError("latest")

    def batch_progress(self, batch_id: str) -> dict[str, Any]:
        raise KeyError(batch_id)

    def batch_artifact(self, batch_id: str, artifact_name: str):
        raise KeyError(batch_id)

    def reload_workers(self) -> dict[str, Any]:
        raise RuntimeError(CONTROL_PLANE_MESSAGE)

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> dict[str, Any]:
        raise RuntimeError(CONTROL_PLANE_MESSAGE)

    # -- writes are refused, never queued --------------------------------------
    def submit(self, payload: dict[str, Any], *, submission_key: str | None = None) -> dict[str, Any]:
        raise _refusal()

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise _refusal()

    def cancel(self, job_id: str) -> dict[str, Any]:
        raise _refusal()

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        raise _refusal()

    def reconcile_result_persistence(self, job_id: str, *, acknowledge: bool = False) -> dict[str, Any]:
        raise _refusal()

    def shutdown(self, wait_seconds: float = 0.0) -> dict[str, Any]:
        return {"accepting_submissions": False, "drained": True, "active_job_id": None}


def _refusal() -> Exception:
    from facebook_backend.domain.exceptions import ServiceUnavailableError

    return ServiceUnavailableError(CONTROL_PLANE_MESSAGE)


def create_control_app(*, settings: Settings | None = None):
    settings = settings or Settings.from_env()
    return create_app(manager=ControlPlaneManager(), settings=settings)


#: Inside a container the process must accept connections from the published
#: port, so it binds the container's own interface.  Exposure stays controlled by
#: compose (``127.0.0.1:8765:8765``) and by the app's Host/Origin guard, which
#: still expects the loopback Host header.
CONTAINER_HOST = "0.0.0.0"


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    settings = Settings.from_env()
    app = create_control_app(settings=settings)
    print(f"Facebook Automation control plane on http://{CONTAINER_HOST}:{settings.port}", flush=True)
    uvicorn.run(app, host=CONTAINER_HOST, port=settings.port, log_level="info",
                access_log=False, workers=1)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
