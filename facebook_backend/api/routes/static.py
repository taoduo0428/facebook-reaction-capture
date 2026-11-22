"""Static UI assets.

The whitelist is taken from the legacy module (``STATIC_ASSETS``) so a new UI
file cannot be served by one server and 404 by the other.  Only the four
known paths are routable; anything else falls through to the JSON 404.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facebook_backend.api.responses import static_response
from facebook_backend.compatibility import legacy

router = APIRouter(tags=["ui"], include_in_schema=False)


def _endpoint(name: str):
    async def serve() -> Response:
        path = Path(legacy.web_dir()) / name
        return await run_in_threadpool(static_response, path)

    serve.__name__ = f"static_{name.replace('.', '_')}"
    return serve


for _request_path, _asset_name in legacy.static_assets().items():
    router.add_api_route(
        _request_path,
        _endpoint(_asset_name),
        methods=["GET"],
        include_in_schema=False,
        name=f"static:{_request_path}",
    )
