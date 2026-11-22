"""Public, read-only projections of the production SQLite coordination tables."""
from __future__ import annotations

import math
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from facebook_backend.domain.exceptions import NotFoundError, ServiceUnavailableError
from facebook_backend.domain.statuses import JOB_STATUSES
from facebook_backend.persistence.database import Database


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", re.ASCII)
_DEVICE_STATES = {"device", "offline", "unauthorized", "booting", "unknown", "adb-not-found"}
_RELEASE_REASONS = JOB_STATUSES | {"takeover-verified", "restart-recovery", "retry-scheduled"}


def _match(pattern, value):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _label(allowed, value):
    return value if value in allowed else (None if value is None else "unknown")


class RuntimeSnapshotService:
    def __init__(self, database: Database):
        self.database = database

    @contextmanager
    def _read(self):
        """Pin one committed WAL snapshot. Never bootstrap a missing store on GET."""
        path = self.database.database_path()
        if not self.database.is_sqlite or not path or path == ":memory:":
            raise ServiceUnavailableError("SQLite runtime projection unavailable")
        uri = Path(path).absolute().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")  # pysqlite does not start a read transaction for SELECT.
                yield conn
        except sqlite3.Error:
            raise ServiceUnavailableError("SQLite runtime projection unavailable") from None

    def devices(self):
        with self._read() as conn:
            rows = conn.execute("""
                SELECT d.id, d.worker_id, d.enabled, d.health_status, d.cooldown_until,
                       d.current_job_id, d.updated_at, l.id AS lease_id
                FROM devices d LEFT JOIN device_leases l
                  ON l.device_id = d.id AND l.released_at IS NULL
                ORDER BY d.worker_id, d.id
            """)
            devices = [{
                "device_id": _match(_ID, r["id"]), "worker_id": _match(_ID, r["worker_id"]),
                "enabled": bool(r["enabled"]), "health_status": _label(_DEVICE_STATES, r["health_status"]) or "unknown",
                "last_heartbeat_at": None, "current_job_id": _match(_ID, r["current_job_id"]),
                "active_lease_id": r["lease_id"], "cooldown_until": _number(r["cooldown_until"]),
                "updated_at": _match(_UTC, r["updated_at"]),
            } for r in rows]
        return {"devices": devices}

    def workers(self):
        # The device table contains the committed worker registrations. A configured
        # but never registered standby is absent until production projects it.
        workers = {}
        for device in self.devices()["devices"]:
            worker_id = device["worker_id"]
            if worker_id is None:
                continue
            worker = workers.setdefault(worker_id, {
                "worker_id": worker_id,
                "device_ids": [],
                "current_job_ids": [],
                "enabled": False,
                "health_status": "unknown",  # Device health does not establish worker-process health.
                "last_heartbeat_at": None,
            })
            worker["enabled"] = worker["enabled"] or device["enabled"]
            if device["device_id"] is not None:
                worker["device_ids"].append(device["device_id"])
            if device["current_job_id"] is not None:
                worker["current_job_ids"].append(device["current_job_id"])
        return {"workers": list(workers.values())}

    def scheduler(self):
        with self._read() as conn:
            counts = dict(conn.execute("SELECT status, count(*) FROM jobs GROUP BY status"))
            held = conn.execute("SELECT count(*) FROM device_leases WHERE released_at IS NULL").fetchone()[0]
        return {"mode": "primary-standby-serial", "max_concurrent_jobs": 1,
                "state": "unknown", "last_heartbeat_at": None, "queue_depth": counts.get("queued", 0),
                "running_jobs": counts.get("running", 0), "cancelling_jobs": counts.get("cancelling", 0),
                "active_leases": held}

    def worker_history(self, worker_id: str, limit: int):
        with self._read() as conn:
            rows = conn.execute("""
                SELECT attempt_id, job_id, attempt_index, started_at, finished_at, result_status
                FROM job_attempts WHERE worker_id = ?
                ORDER BY started_at DESC, job_id DESC, attempt_index DESC LIMIT ?
            """, (worker_id, limit))
            attempts = [{
                "attempt_id": _match(_ID, r["attempt_id"]), "job_id": _match(_ID, r["job_id"]),
                "attempt_index": _number(r["attempt_index"]), "started_at": _match(_UTC, r["started_at"]),
                "finished_at": _match(_UTC, r["finished_at"]), "result_status": _label(JOB_STATUSES, r["result_status"]),
            } for r in rows]
            if not attempts and not conn.execute("SELECT 1 FROM devices WHERE worker_id = ? LIMIT 1", (worker_id,)).fetchone():
                raise NotFoundError("worker not found")
        return {"worker_id": worker_id, "attempts": attempts}

    def lease_history(self, limit: int):
        with self._read() as conn:
            rows = conn.execute("""
                SELECT id, device_id, worker_id, job_id, fencing_version, acquired_at, heartbeat_at,
                       expires_at, released_at, release_reason FROM device_leases ORDER BY id DESC LIMIT ?
            """, (limit,))
            leases = [{
                "lease_id": r["id"], "device_id": _match(_ID, r["device_id"]),
                "worker_id": _match(_ID, r["worker_id"]), "job_id": _match(_ID, r["job_id"]),
                "fencing_version": _number(r["fencing_version"]), "state": "held" if r["released_at"] is None else "released",
                "acquired_at": _match(_UTC, r["acquired_at"]), "expires_at": _match(_UTC, r["expires_at"]),
                # acquire() seeds heartbeat_at with exactly acquired_at. Only an
                # independent renewal is evidence of a subsequent lease heartbeat.
                "heartbeat_at": _match(_UTC, r["heartbeat_at"]) if r["acquired_at"] != r["heartbeat_at"] else None,
                "released_at": _match(_UTC, r["released_at"]), "release_reason": _label(_RELEASE_REASONS, r["release_reason"]),
            } for r in rows]
        return {"leases": leases}
