"""Observe ASGI requests without reading request bodies or changing responses."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from starlette.concurrency import run_in_threadpool

from facebook_backend.observability.context import bound
from facebook_backend.observability.logging import log_event
from facebook_backend.observability.metrics import METRICS, PlatformMetrics
from facebook_backend.observability.tracing import ensure_tracing, entry_span

LOGGER = logging.getLogger("facebook_backend.api")
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"})
UNMATCHED = "unmatched"


def request_route(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    methods = getattr(route, "methods", None)
    if methods and scope.get("method") not in methods:
        return UNMATCHED  # Starlette may set route for a partial (method-only mismatch) match.
    return getattr(route, "path", None) or UNMATCHED


def _traceparent(scope: dict[str, Any]) -> str | None:
    value = None
    for key, raw in scope.get("headers", ()):
        if key.lower() == b"traceparent":
            if value is not None or len(raw) != 55 or not raw.isascii():
                return None
            value = raw.decode("ascii")
    return value


class ObservabilityMiddleware:
    def __init__(self, app, *, metrics: PlatformMetrics | None = None):
        self.app = app
        self.metrics = metrics or METRICS

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await run_in_threadpool(ensure_tracing)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method")
        method = method if method in _METHODS else "OTHER"
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with bound(request_id=uuid.uuid4().hex[:16]):
            with entry_span("http.request", _traceparent(scope), http_method=method) as active:
                try:
                    await self.app(scope, receive, send_wrapper)
                finally:
                    duration = time.perf_counter() - started
                    route = request_route(scope)
                    active.set_attribute("http_route", route)
                    active.set_attribute("http_status", status)
                    self.metrics.api_requests.inc(method=method, route=route, status=str(status))
                    self.metrics.api_latency.observe(duration, method=method, route=route)
                    log_event(LOGGER, "http.request", http_method=method, http_route=route,
                              http_status=status, duration_ms=round(duration * 1000, 2))


__all__ = ["ObservabilityMiddleware", "request_route"]
