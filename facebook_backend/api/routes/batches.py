"""Batch endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from facebook_backend.api.request_body import read_json_body
from facebook_backend.api.responses import artifact_response, json_response
from facebook_backend.dependencies import get_artifact_service, get_batch_service
from facebook_backend.services.artifact_service import ArtifactService
from facebook_backend.services.batch_service import BatchService

router = APIRouter(tags=["batches"])


def _download(request: Request) -> bool:
    return request.scope.get("query_string") == b"download=1"


@router.post("/v1/batches", status_code=202, summary="Submit a batch (202 Accepted)")
async def create_batch(
    request: Request,
    service: BatchService = Depends(get_batch_service),
) -> Response:
    payload = await read_json_body(request)
    result = await run_in_threadpool(service.submit, payload)
    return json_response(result, status_code=202)


@router.get("/v1/batches/latest", summary="Most recently created batch")
async def latest_batch(service: BatchService = Depends(get_batch_service)) -> Response:
    return json_response(await run_in_threadpool(service.latest))


@router.get("/v1/batches/{batch_id}/artifacts/{artifact_name}", summary="Download a batch artifact")
async def batch_artifact(
    request: Request,
    batch_id: str,
    artifact_name: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> Response:
    artifact = await run_in_threadpool(
        service.batch_artifact, batch_id, artifact_name, download=_download(request)
    )
    return artifact_response(artifact)


@router.get("/v1/batches/{batch_id}", summary="Batch progress summary")
async def get_batch(batch_id: str, service: BatchService = Depends(get_batch_service)) -> Response:
    return json_response(await run_in_threadpool(service.progress, batch_id))


@router.get("/v1/batches/{batch_id}/progress", summary="Batch progress summary")
async def batch_progress(
    batch_id: str, service: BatchService = Depends(get_batch_service)
) -> Response:
    return json_response(await run_in_threadpool(service.progress, batch_id))


@router.post("/v1/batches/{batch_id}/cancel", summary="Cancel every unfinished job in a batch")
async def cancel_batch(
    batch_id: str, service: BatchService = Depends(get_batch_service)
) -> Response:
    return json_response(await run_in_threadpool(service.cancel, batch_id))
