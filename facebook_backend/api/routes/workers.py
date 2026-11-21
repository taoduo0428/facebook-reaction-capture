"""Worker control-plane endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facebook_backend.api.responses import json_response
from facebook_backend.dependencies import get_worker_service
from facebook_backend.services.worker_service import WorkerService

router = APIRouter(tags=["workers"])


@router.post("/v1/workers/reload", summary="Reload facebook_workers.json")
async def reload_workers(service: WorkerService = Depends(get_worker_service)) -> Response:
    return json_response(await run_in_threadpool(service.reload))


@router.post("/v1/workers/{worker_id}/enable", summary="Enable a standby worker")
async def enable_worker(
    worker_id: str, service: WorkerService = Depends(get_worker_service)
) -> Response:
    return json_response(await run_in_threadpool(service.set_enabled, worker_id, True))


@router.post("/v1/workers/{worker_id}/disable", summary="Disable a worker")
async def disable_worker(
    worker_id: str, service: WorkerService = Depends(get_worker_service)
) -> Response:
    return json_response(await run_in_threadpool(service.set_enabled, worker_id, False))
