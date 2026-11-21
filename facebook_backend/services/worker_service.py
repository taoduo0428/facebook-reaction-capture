"""Worker control-plane use-cases."""
from __future__ import annotations

from typing import Any

from facebook_backend.compatibility.legacy import map_legacy_errors
from facebook_backend.domain.ports import JobManagerPort

RELOAD_SCHEDULER_ERROR = "worker reload could not start the scheduler; inspect state before retrying"
RELOAD_IO_ERROR = "worker reload failed due to a local I/O error"
UPDATE_SCHEDULER_ERROR = "worker update could not start the scheduler; inspect state before retrying"
UPDATE_IO_ERROR = "worker update failed due to a local I/O error"


class WorkerService:
    def __init__(self, manager: JobManagerPort):
        self.manager = manager

    def reload(self) -> dict[str, Any]:
        with map_legacy_errors(
            api_status=409,
            scheduler=RELOAD_SCHEDULER_ERROR,
            os_error=RELOAD_IO_ERROR,
        ):
            return self.manager.reload_workers()

    def set_enabled(self, worker_id: str, enabled: bool) -> dict[str, Any]:
        with map_legacy_errors(
            not_found="worker not found",
            api_status=409,
            scheduler=UPDATE_SCHEDULER_ERROR,
            os_error=UPDATE_IO_ERROR,
        ):
            return self.manager.set_worker_enabled(worker_id, enabled)
