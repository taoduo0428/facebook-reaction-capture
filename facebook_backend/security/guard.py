"""HTTP trust boundary for the FastAPI backend.

The legacy ``ThreadingHTTPServer`` handler enforced three boundaries before any
state change:

1. ``facebook_http_security.request_error`` — origin-form request target,
   exactly one loopback ``Host`` matching the listening port, same-origin
   ``Origin`` and no ``Sec-Fetch-Site: cross-site``.
2. ``facebook_http_security.body_length`` — no ``Transfer-Encoding``, exactly
   one numeric ``Content-Length``, at most 64 KiB.
3. Per-route framing (empty body, truncated body, strict JSON).

This module keeps (1) and (2) byte-for-byte by re-using the shared security
module through a tiny adapter object, so the rules cannot drift between the
legacy server and the new one.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from facebook_http_security import body_length, request_error

#: ASGI scope key holding the raw header list.
HeaderList = Iterable[tuple[bytes, bytes]]


class HeaderBag:
    """``email.message.Message``-compatible read-only view over ASGI headers."""

    __slots__ = ("_raw",)

    def __init__(self, raw: HeaderList | None):
        self._raw = tuple(raw or ())

    def get_all(self, name: str, failobj: Any = None) -> Any:
        # ASGI header names are lower-cased ``bytes``; compare in the same
        # domain instead of mixing ``str`` and ``bytes`` (which never matches).
        target = name.lower().encode("latin-1")
        values = [
            value.decode("latin-1") for key, value in self._raw if key.lower() == target
        ]
        if values:
            return values
        return failobj

    def get(self, name: str, default: Any = None) -> Any:
        values = self.get_all(name)
        return values[0] if values else default

    def raw_items(self) -> tuple[tuple[bytes, bytes], ...]:
        return self._raw


class _ServerView:
    __slots__ = ("server_port",)

    def __init__(self, server_port: int):
        self.server_port = server_port


class _RequestView:
    """The slice of ``BaseHTTPRequestHandler`` that ``request_error`` reads."""

    __slots__ = ("path", "headers", "server")

    def __init__(self, path: str, headers: HeaderBag, server_port: int):
        self.path = path
        self.headers = headers
        self.server = _ServerView(server_port)


def request_target(scope: dict[str, Any]) -> str:
    """Rebuild the raw origin-form request target (``path`` + ``?query``)."""
    raw = scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)) and raw:
        path = bytes(raw).decode("latin-1")
    else:
        path = str(scope.get("path") or "/")
    query = scope.get("query_string") or b""
    if isinstance(query, (bytes, bytearray)) and query:
        return path + "?" + bytes(query).decode("latin-1")
    return path


def server_port(scope: dict[str, Any], default: int) -> int:
    server = scope.get("server")
    if isinstance(server, (tuple, list)) and len(server) >= 2 and server[1]:
        try:
            return int(server[1])
        except (TypeError, ValueError):
            return default
    return default


def evaluate_request(scope: dict[str, Any], *, default_port: int) -> tuple[int, str] | None:
    """Return ``(status, message)`` when the request must be rejected."""
    view = _RequestView(
        request_target(scope),
        HeaderBag(scope.get("headers")),
        server_port(scope, default_port),
    )
    return request_error(view)


def evaluate_framing(scope: dict[str, Any]) -> tuple[int, str] | None:
    """Apply the legacy body-framing rules to a POST request."""
    try:
        body_length(HeaderBag(scope.get("headers")))
    except ValueError as exc:
        return 400, str(exc)
    return None


def json_bytes(payload: Any) -> bytes:
    """Serialise exactly like the legacy handler (compact, non-ASCII kept)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def send_json(send, status: int, payload: Any) -> None:
    body = json_bytes(payload)
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class TrustBoundaryMiddleware:
    """Pure ASGI middleware: no body buffering, no response wrapping."""

    def __init__(self, app, *, default_port: int):
        self.app = app
        self.default_port = default_port

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        error = evaluate_request(scope, default_port=self.default_port)
        if error is not None:
            return await send_json(send, error[0], {"error": error[1]})
        if str(scope.get("method") or "").upper() == "POST":
            error = evaluate_framing(scope)
            if error is not None:
                return await send_json(send, error[0], {"error": error[1]})
        return await self.app(normalize_path_scope(scope), receive, send)


def normalize_path_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Collapse empty path segments the way the legacy ``_segments()`` did.

    ``BaseHTTPRequestHandler`` split the target and dropped empty parts, so
    ``/health/``, ``/health//`` and ``/health`` were the same route.  Starlette
    would answer 404/307 instead, so the path is normalised before routing.
    """
    path = str(scope.get("path") or "/")
    parts = [part for part in path.split("/") if part]
    normalized = "/" + "/".join(parts) if parts else "/"
    if normalized == path:
        return scope
    normalized_scope = dict(scope)
    normalized_scope["path"] = normalized
    normalized_scope["raw_path"] = normalized.encode("utf-8")
    return normalized_scope
