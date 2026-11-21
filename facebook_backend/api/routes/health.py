"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facebook_backend.api.responses import json_response
from facebook_backend.dependencies import get_job_service
from facebook_backend.services.job_service import JobService

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service, scheduler, worker and device health")
async def health(service: JobService = Depends(get_job_service)) -> Response:
    # ``health()`` shells out to ADB and inspects scheduler threads: it is a
    # blocking call and must never run on the event loop.
    return json_response(await run_in_threadpool(service.health))
