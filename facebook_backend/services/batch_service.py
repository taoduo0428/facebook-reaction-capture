"""Batch use-cases and their error contract."""
from __future__ import annotations

from typing import Any

from facebook_backend.compatibility.legacy import map_legacy_errors
from facebook_backend.domain.ports import JobManagerPort
from facebook_backend.services.job_service import SUBMIT_IO_ERROR


class BatchService:
    def __init__(self, manager: JobManagerPort):
        self.manager = manager

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        with map_legacy_errors(api_status=400, os_error=SUBMIT_IO_ERROR, decode_status=400):
            return self.manager.submit_batch(payload)

    def latest(self) -> dict[str, Any]:
        with map_legacy_errors(not_found="no batches found"):
            return self.manager.latest_batch()

    def progress(self, batch_id: str) -> dict[str, Any]:
        # The legacy handler only translated ``KeyError`` here; any other
        # failure stays a real 500 instead of being dressed up as "not found".
        with map_legacy_errors(not_found="batch not found"):
            return self.manager.batch_progress(batch_id)

    def cancel(self, batch_id: str) -> dict[str, Any]:
        with map_legacy_errors(not_found="batch not found"):
            return self.manager.cancel_batch(batch_id)
