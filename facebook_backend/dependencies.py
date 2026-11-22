"""FastAPI dependency providers.

The manager lives on ``app.state`` and is created exactly once by the lifespan
handler.  Dependencies never construct a manager themselves.
"""
from __future__ import annotations

from starlette.requests import Request

from facebook_backend.domain.exceptions import ServiceUnavailableError
from facebook_backend.domain.ports import JobManagerPort
from facebook_backend.services.artifact_service import ArtifactService
from facebook_backend.services.batch_service import BatchService
from facebook_backend.services.job_service import JobService
from facebook_backend.services.worker_service import WorkerService


def get_manager(request: Request) -> JobManagerPort:
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise ServiceUnavailableError("job manager is not initialized")
    return manager


def get_job_service(request: Request) -> JobService:
    return JobService(get_manager(request))


def get_batch_service(request: Request) -> BatchService:
    return BatchService(get_manager(request))


def get_worker_service(request: Request) -> WorkerService:
    return WorkerService(get_manager(request))


def get_artifact_service(request: Request) -> ArtifactService:
    return ArtifactService(get_manager(request))
