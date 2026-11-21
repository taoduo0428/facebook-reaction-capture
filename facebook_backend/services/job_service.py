"""Job use-cases.

The service owns the *error contract* for jobs so routers stay declarative:
the same legacy exception always becomes the same HTTP status and message,
regardless of which route triggered it.
"""
from __future__ import annotations

from typing import Any

from facebook_backend.compatibility.legacy import map_legacy_errors
from facebook_backend.domain.exceptions import ServiceUnavailableError
from facebook_backend.domain.ports import JobManagerPort

SUBMIT_IO_ERROR = "capture submission failed due to a local I/O error; inspect state before retrying"
SHUTTING_DOWN = "service is shutting down; no new jobs are accepted"
RECONCILE_SCHEDULER_ERROR = (
    "result reconciliation could not start the scheduler; inspect state before retrying"
)
RECONCILE_IO_ERROR = (
    "result reconciliation failed due to a local I/O error; inspect state before retrying"
)


class JobService:
    def __init__(self, manager: JobManagerPort):
        self.manager = manager

    def health(self) -> dict[str, Any]:
        """Service/scheduler/worker/device health (blocking: uses ADB)."""
        return self.manager.health()

    def submit(
        self, payload: dict[str, Any], *, submission_key: str | None = None
    ) -> dict[str, Any]:
        # Refuse before creating state: a shutdown must not accept new work.
        if getattr(self.manager, "accepting_submissions", True) is False:
            raise ServiceUnavailableError(SHUTTING_DOWN)
        with map_legacy_errors(api_status=400, os_error=SUBMIT_IO_ERROR, decode_status=400):
            if submission_key:
                return self.manager.submit(payload, submission_key=submission_key)
            return self.manager.submit(payload)

    def latest(self) -> dict[str, Any]:
        with map_legacy_errors(not_found="no jobs found"):
            return self.manager.latest_job()

    def get(self, job_id: str) -> dict[str, Any]:
        with map_legacy_errors(not_found="job not found", api_status=409):
            return self.manager.public_job(job_id)

    def progress(self, job_id: str) -> dict[str, Any]:
        with map_legacy_errors(not_found="job not found", api_status=409):
            return self.manager.progress(job_id)

    def result(self, job_id: str) -> dict[str, Any]:
        with map_legacy_errors(not_found="job not found", api_status=409):
            return self.manager.result(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with map_legacy_errors(not_found="job not found"):
            return self.manager.cancel(job_id)

    def reconcile_result_persistence(self, job_id: str) -> dict[str, Any]:
        with map_legacy_errors(
            not_found="job not found",
            api_status=409,
            scheduler=RECONCILE_SCHEDULER_ERROR,
            os_error=RECONCILE_IO_ERROR,
        ):
            return self.manager.reconcile_result_persistence(job_id, acknowledge=True)
