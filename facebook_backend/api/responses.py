"""Response builders that reproduce the legacy wire format exactly.

Legacy behaviour that must be preserved:

* JSON bodies are ``json.dumps(..., ensure_ascii=False, separators=(",", ":"))``
  (compact, non-ASCII preserved) with ``application/json; charset=utf-8``,
  ``Cache-Control: no-store`` and ``X-Content-Type-Options: nosniff``.
* Artifacts stream in 1 MiB chunks with an explicit ``Content-Length`` and a
  ``Content-Disposition`` that is ``inline`` only for text-like suffixes.
* Static assets carry the same strict CSP as before.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterator

from starlette.responses import Response, StreamingResponse

from facebook_backend.domain.exceptions import NotFoundError
from facebook_backend.security.guard import json_bytes
from facebook_backend.services.artifact_service import ArtifactFile

JSON_MEDIA_TYPE = "application/json; charset=utf-8"
STATIC_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'"
)


class ApiJSONResponse(Response):
    media_type = JSON_MEDIA_TYPE

    def __init__(self, content, status_code: int = 200, headers: dict[str, str] | None = None):
        super().__init__(
            content=json_bytes(content),
            status_code=status_code,
            media_type=JSON_MEDIA_TYPE,
            headers=headers,
        )
        self.headers.setdefault("cache-control", "no-store")
        self.headers.setdefault("x-content-type-options", "nosniff")


def json_response(payload, status_code: int = 200) -> ApiJSONResponse:
    return ApiJSONResponse(payload, status_code=status_code)


def _chunks(path: Path, size: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(size)
            if not chunk:
                break
            yield chunk


def artifact_response(artifact: ArtifactFile) -> StreamingResponse:
    # ``content-type`` is supplied verbatim: passing it as ``media_type`` would
    # let Starlette append ``; charset=utf-8`` to text types, which the legacy
    # server never did for artifacts.
    headers = {
        "content-type": artifact.content_type,
        "content-length": str(artifact.size),
        "content-disposition": f'{artifact.disposition}; filename="{artifact.path.name}"',
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }
    return StreamingResponse(_chunks(artifact.path, artifact.chunk_size), headers=headers)


def _content_type(name: str) -> str:
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
    }:
        content_type += "; charset=utf-8"
    return content_type


def static_response(path: Path) -> Response:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise NotFoundError("asset not found") from exc
    return Response(
        content=body,
        media_type=_content_type(path.name),
        headers={
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": STATIC_CSP,
        },
    )
