"""Setup, capability and guide endpoints (identical to the legacy handler)."""
from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facebook_backend.api.responses import json_response
from facebook_backend.compatibility import legacy
from facebook_backend.domain.exceptions import BackendError

router = APIRouter(tags=["setup"])


@router.get("/v1/setup", summary="Local environment doctor")
async def setup_doctor() -> Response:
    try:
        report = await run_in_threadpool(legacy.doctor)
    except (OSError, ValueError, RuntimeError) as exc:
        raise BackendError(str(exc), status_code=400) from exc
    return json_response(report)


@router.get("/v1/capabilities", summary="Platform capture capabilities")
async def capabilities() -> Response:
    return json_response(legacy.capabilities())


@router.get("/guide", summary="Project README as text/plain", include_in_schema=False)
async def guide() -> Response:
    path = legacy.web_dir().parent / "README.md"
    try:
        body = await run_in_threadpool(path.read_bytes)
    except OSError as exc:
        raise BackendError("guide is not available", status_code=500) from exc
    return Response(content=body, media_type="text/plain; charset=utf-8")
