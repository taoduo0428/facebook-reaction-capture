"""Request-body handling with the legacy framing contract.

``TrustBoundaryMiddleware`` already rejected ``Transfer-Encoding``, ambiguous
``Content-Length`` and oversized bodies.  What remains is the per-route framing
the legacy handler performed: an empty body is invalid for submissions, a body
shorter than its declared length is invalid, JSON parse errors surface as 400
with the parser message, and the result-reconciliation endpoint only accepts a
byte-exact ``{"acknowledge": true}`` document.
"""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import ClientDisconnect, Request

from facebook_backend.domain.exceptions import BadRequestError, ServiceUnavailableError
from facebook_backend.security.guard import HeaderBag
from facebook_http_security import MAX_REQUEST_BYTES, body_length

ACK_INVALID = "invalid explicit acknowledgment body"


def declared_length(request: Request) -> int:
    try:
        return body_length(HeaderBag(request.scope.get("headers")))
    except ValueError as exc:  # defence in depth: the middleware already ran
        raise BadRequestError(str(exc)) from exc


async def _read_exact(request: Request, *, incomplete_message: str) -> bytes:
    length = declared_length(request)
    try:
        raw = await request.body()
    except ClientDisconnect as exc:
        raise BadRequestError(incomplete_message) from exc
    if len(raw) != length:
        raise BadRequestError(incomplete_message)
    return raw


async def read_json_body(request: Request) -> Any:
    """Parse a submission body exactly like the legacy handler."""
    length = declared_length(request)
    if length <= 0 or length > MAX_REQUEST_BYTES:
        raise BadRequestError("invalid request body size")
    raw = await _read_exact(request, incomplete_message="incomplete request body")
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BadRequestError(str(exc)) from exc


def _unique_object(pairs):
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate acknowledgment keys are not allowed")
        value[key] = item
    return value


async def require_explicit_acknowledgment(request: Request) -> None:
    """Accept only ``Content-Type: application/json`` and ``{"acknowledge": true}``.

    The acknowledgment is deliberately explicit: it is never inferred from a
    GET, a worker toggle, a repeated cleanup or permissive coercion.
    """
    content_type = (HeaderBag(request.scope.get("headers")).get("Content-Type") or "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    length = declared_length(request)
    if length <= 0:
        raise BadRequestError(ACK_INVALID)
    try:
        raw = await request.body()
    except ClientDisconnect as exc:
        raise BadRequestError(ACK_INVALID) from exc
    except OSError as exc:  # pragma: no cover - transport level abort
        raise ServiceUnavailableError(
            "acknowledgment request failed due to a local I/O error"
        ) from exc
    if len(raw) != length:
        raise BadRequestError(ACK_INVALID)
    if content_type != "application/json":
        raise BadRequestError(ACK_INVALID)
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise BadRequestError(ACK_INVALID) from exc
    if not isinstance(payload, dict) or set(payload) != {"acknowledge"} or payload["acknowledge"] is not True:
        raise BadRequestError(ACK_INVALID)
