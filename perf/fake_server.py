"""Load-test target: the FastAPI app with an in-memory fake manager.

This is **not** production code and it does not touch a device.  It exists so the
HTTP layer can be measured without an emulator: every endpoint returns a small
deterministic document, and submissions are stored in a dict.

The launcher owns the loopback socket and supplies a fresh identity for each run.
"""
from __future__ import annotations

import argparse
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from facebook_backend.config import Settings  # noqa: E402
from facebook_backend.main import create_app  # noqa: E402

JOB_TEMPLATE: dict[str, Any] = {
    "status": "queued",
    "created_at": "2026-01-01T00:00:00Z",
    "started_at": None,
    "finished_at": None,
    "cancel_requested_at": None,
    "request": {"post_url": "https://www.facebook.com/x/posts/1", "collect": ["reactions"]},
    "completion_scope": None,
    "error": None,
    "run_dir": "runs/perf",
    "attempt_dirs": [],
    "batch_id": None,
    "batch_index": None,
    "worker_id": None,
    "serial": None,
    "attempts": 0,
    "failover_count": 0,
    "process_state": None,
    "transport_cleanup_state": None,
    "partial_evidence_available": False,
}


class InMemoryManager:
    """Thread-safe fake manager covering the endpoints k6 exercises."""

    def __init__(self) -> None:
        self.accepting_submissions = True
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    def health(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for job in self._jobs.values() if job["status"] == "queued")
        return {
            "service": "ready",
            "mode": "perf-fake",
            "scheduler": {"state": "running", "alive": True},
            "worker": "idle",
            "queued_jobs": queued,
            "cancelling_jobs": 0,
            "android": {"state": "device", "boot_completed": True, "proxy": ""},
            "serial": "emulator-5554",
            "active_worker_id": "worker-00",
            "capture_lease": {"state": "available", "job_id": None, "reason": None},
            "workers": [],
        }

    def submit(self, payload: dict[str, Any], *, submission_key: str | None = None) -> dict[str, Any]:
        job_id = f"fb_{uuid.uuid4().hex[:16]}"
        job = dict(JOB_TEMPLATE, job_id=job_id, request=dict(payload))
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            if len(self._order) > 5_000:  # bound memory during a long run
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        return dict(job)

    def latest_job(self) -> dict[str, Any]:
        with self._lock:
            if not self._order:
                raise KeyError("latest")
            return dict(self._jobs[self._order[-1]])

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return dict(job)

    def progress(self, job_id: str) -> dict[str, Any]:
        job = self.public_job(job_id)
        return {
            "job_status": job["status"],
            "storage_root": "runs/perf",
            "metrics": {"valid_reactor_users": 0},
            "stages": [],
            "artifacts": [],
        }

    def result(self, job_id: str) -> dict[str, Any]:
        self.public_job(job_id)
        return {"run_dir": "runs/perf", "result_outcome": "complete", "reaction_users": 0}

    def artifact(self, job_id: str, artifact_name: str) -> Path:
        self.public_job(job_id)
        raise KeyError(artifact_name)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.public_job(job_id)
        with self._lock:
            self._jobs[job_id] = dict(job, status="cancelled")
        return {"cancelled": True, "reason": "queued job cancelled", "job": dict(self._jobs[job_id])}

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"batch_id": "batch_perf", "status": "queued", "total_posts": 1, "posts": []}

    def latest_batch(self) -> dict[str, Any]:
        raise KeyError("latest")

    def batch_progress(self, batch_id: str) -> dict[str, Any]:
        return {"batch_id": batch_id, "status": "queued", "total_posts": 0, "posts": []}

    def batch_artifact(self, batch_id: str, artifact_name: str) -> Path:
        raise KeyError(artifact_name)

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        return {"cancelled_jobs": 0, "batch": {"batch_id": batch_id}}

    def reload_workers(self) -> dict[str, Any]:
        return {"added": [], "updated": [], "worker_ids": []}

    def set_worker_enabled(self, worker_id: str, enabled: bool) -> dict[str, Any]:
        return {"worker_id": worker_id, "enabled": enabled}

    def reconcile_result_persistence(self, job_id: str, *, acknowledge: bool = False) -> dict[str, Any]:
        return {"job_id": job_id, "reconciled": True}


def build_app(key: str, identity: str, root: Path, port: int):
    from fastapi.responses import JSONResponse
    import secrets

    manager = InMemoryManager()
    seed = manager.submit({"post_url": "fixture:synthetic"})
    app = create_app(manager=manager, settings=Settings(base_dir=root, web_dir=root,
                     port=port, load_local_config=False, log_level="WARNING"))

    @app.middleware("http")
    async def require_fixture(request, call_next):
        if not secrets.compare_digest(request.headers.get("x-fixture-key", ""), key):
            return JSONResponse({}, status_code=403)
        response = await call_next(request)
        response.headers["x-fixture-id"] = identity
        return response

    @app.get("/__fixture")
    def probe():
        import os
        return {"job_id": seed["job_id"], "pid": os.getpid()}

    return app


def serve(sock, key: str, identity: str, root: str, stop) -> None:
    """Spawn entry. The parent retains its socket until this process exits."""
    import uvicorn

    port = sock.getsockname()[1]
    app = build_app(key, identity, Path(root), port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False, proxy_headers=False))

    def watch_stop():
        stop.wait()
        server.should_exit = True

    threading.Thread(target=watch_stop, daemon=True).start()
    server.run(sockets=[sock])
