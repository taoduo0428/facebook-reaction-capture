"""Job endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from facebook_backend.api.request_body import read_json_body, require_explicit_acknowledgment
from facebook_backend.api.responses import artifact_response, json_response
from facebook_backend.dependencies import get_artifact_service, get_job_service
from facebook_backend.domain.exceptions import BadRequestError
from facebook_backend.schemas.job import JobSubmission
from facebook_backend.services.artifact_service import ArtifactService
from facebook_backend.services.job_service import JobService

router = APIRouter(tags=["jobs"])

# Validation stays in ``normalize_request`` (legacy error messages are part of
# the contract); the schema below only documents the accepted body in OpenAPI.
JOB_BODY_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": JobSubmission.model_json_schema()}},
    }
}

ACK_BODY_OPENAPI = {
    "requestBody": {
        "required": True,
        "description": "Exactly one boolean field: {\"acknowledge\": true}",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["acknowledge"],
                    "properties": {"acknowledge": {"type": "boolean", "const": True}},
                }
            }
        },
    }
}


def _download(request: Request) -> bool:
    # Legacy used ``urlparse(path).query == "download=1"`` — an exact match on
    # the whole query string, not a parsed flag.
    return request.scope.get("query_string") == b"download=1"


@router.post(
    "/v1/jobs",
    status_code=202,
    summary="Submit a capture job (202 Accepted)",
    openapi_extra=JOB_BODY_OPENAPI,
)
async def create_job(
    request: Request,
    service: JobService = Depends(get_job_service),
) -> Response:
    payload = await read_json_body(request)
    submission_key = _submission_key(request)
    result = await run_in_threadpool(service.submit, payload, submission_key=submission_key)
    return json_response(result, status_code=202)


def _submission_key(request: Request) -> str | None:
    """``Idempotency-Key`` makes a retried POST return the original job."""
    raw = (request.headers.get("idempotency-key") or "").strip()
    if not raw:
        return None
    if len(raw) > 200:
        raise BadRequestError("idempotency key is too long")
    return raw


@router.get("/v1/jobs/latest", summary="Most recently created job")
async def latest_job(service: JobService = Depends(get_job_service)) -> Response:
    return json_response(await run_in_threadpool(service.latest))


@router.get("/v1/jobs/{job_id}/artifacts/{artifact_name}", summary="Download a job artifact")
async def job_artifact(
    request: Request,
    job_id: str,
    artifact_name: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> Response:
    artifact = await run_in_threadpool(
        service.job_artifact, job_id, artifact_name, download=_download(request)
    )
    return artifact_response(artifact)


@router.get("/v1/jobs/{job_id}", summary="Public job state")
async def get_job(job_id: str, service: JobService = Depends(get_job_service)) -> Response:
    return json_response(await run_in_threadpool(service.get, job_id))


@router.get("/v1/jobs/{job_id}/progress", summary="Job progress snapshot")
async def job_progress(job_id: str, service: JobService = Depends(get_job_service)) -> Response:
    return json_response(await run_in_threadpool(service.progress, job_id))


@router.get("/v1/jobs/{job_id}/result", summary="Verified job result")
async def job_result(job_id: str, service: JobService = Depends(get_job_service)) -> Response:
    return json_response(await run_in_threadpool(service.result, job_id))


@router.post("/v1/jobs/{job_id}/cancel", summary="Request cooperative cancellation")
async def cancel_job(job_id: str, service: JobService = Depends(get_job_service)) -> Response:
    return json_response(await run_in_threadpool(service.cancel, job_id))


@router.post(
    "/v1/jobs/{job_id}/reconcile-result-persistence",
    summary="Explicitly acknowledge result-persistence reconciliation",
    openapi_extra=ACK_BODY_OPENAPI,
)
async def reconcile_result_persistence(
    request: Request,
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> Response:
    await require_explicit_acknowledgment(request)
    result = await run_in_threadpool(service.reconcile_result_persistence, job_id)
    return json_response(result)
