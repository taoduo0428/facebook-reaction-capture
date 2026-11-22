"""Operations snapshots from the SQLite database attached during app startup."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from facebook_backend.api.responses import json_response
from facebook_backend.domain.exceptions import ServiceUnavailableError
from facebook_backend.services.runtime_snapshot import RuntimeSnapshotService

router = APIRouter(prefix="/v1", tags=["runtime"])
HistoryLimit = Annotated[int, Query(ge=1, le=100, description="Maximum recent records")]
WorkerID = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


def get_runtime_service(request: Request) -> RuntimeSnapshotService:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise ServiceUnavailableError("SQLite runtime projection unavailable")
    return RuntimeSnapshotService(database)


Service = Annotated[RuntimeSnapshotService, Depends(get_runtime_service)]


@router.get("/workers", summary="Committed worker registrations")
def workers(service: Service) -> Response:
    return json_response(service.workers())


@router.get("/devices", summary="Last recorded device snapshots")
def devices(service: Service) -> Response:
    return json_response(service.devices())


@router.get("/scheduler", summary="Serial scheduler policy, job counts and lease count")
def scheduler(service: Service) -> Response:
    return json_response(service.scheduler())


@router.get("/workers/{worker_id}/history", summary="Recent attempts for a worker")
def worker_history(worker_id: WorkerID, service: Service, limit: HistoryLimit = 20) -> Response:
    return json_response(service.worker_history(worker_id, limit))


@router.get("/leases/history", summary="Recent device leases in acquisition order, newest first")
def lease_history(service: Service, limit: HistoryLimit = 20) -> Response:
    return json_response(service.lease_history(limit))
