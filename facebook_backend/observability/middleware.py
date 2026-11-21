"""ASGI middleware: one span, one log line and two metrics per request.

The middleware never touches the response body or headers — it only observes —
so the byte-for-byte contract tests keep passing.  It also binds a ``request_id``
for the whole request and reads the live trace id from OpenTelemetry, which is
what makes "which log line belongs to which trace" answerable.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from facebook_backend.observability.context import bind, reset
from facebook_backend.observability.logging import log_event
from facebook_backend.observability.metrics import METRICS, PlatformMetrics
from facebook_backend.observability.tracing import current_trace_id, span

LOGGER = logging.getLogger("facebook_backend.api")


class ObservabilityMiddleware:
    def __init__(self, app, *, metrics: PlatformMetrics | None = None):
        self.app = app
        self.metrics = metrics or METRICS

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        started = time.perf_counter()
        status_holder: dict[str, int] = {}
        request_id = uuid.uuid4().hex[:16]
        token = bind(request_id=request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
            await send(message)

        try:
            with span("http.request", http_method=method, http_path=path):
                await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", None) or path
            status = status_holder.get("status", 500)
            trace_id = current_trace_id()
            try:
                self.metrics.api_requests.inc(method=method, route=route, status=str(status))
                self.metrics.api_latency.observe(duration, method=method, route=route)
            finally:
                log_event(
                    LOGGER,
                    "http.request",
                    http_method=method,
                    http_route=route,
                    http_status=status,
                    duration_ms=round(duration * 1000, 2),
                    trace_id=trace_id,
                )
                reset(token)


def request_route(scope: dict[str, Any]) -> str:
    return getattr(scope.get("route"), "path", None) or str(scope.get("path") or "/")


__all__ = ["ObservabilityMiddleware", "request_route"]
