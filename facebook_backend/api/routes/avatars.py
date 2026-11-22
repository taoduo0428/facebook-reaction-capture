"""Avatar proxy: 302 to the resolved public profile picture URL."""
from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facebook_backend.compatibility import legacy
from facebook_backend.domain.exceptions import ServiceUnavailableError

router = APIRouter(tags=["avatars"])


@router.get("/v1/avatars/{user_id}", summary="Redirect to a profile picture")
async def avatar(user_id: str) -> Response:
    try:
        # Network call: never on the event loop.
        location = await run_in_threadpool(legacy.resolve_avatar_url, user_id)
    except Exception as exc:  # noqa: BLE001 - mapped below
        if legacy.is_legacy_api_error(exc):
            raise ServiceUnavailableError(str(exc)) from exc
        raise
    return Response(
        status_code=302,
        headers={"location": location, "cache-control": "private, max-age=300"},
    )
