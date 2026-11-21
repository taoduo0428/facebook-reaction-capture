"""FastAPI application factory.

Lifespan contract (see docs/backend-modernization/ARCHITECTURE.md):

* Exactly one ``JobManager`` exists per process, created during startup and
  stored on ``app.state.manager``.
* No manager, scheduler thread or capture subprocess is created at import time.
* ``uvicorn`` must run a single process (``workers=1``): the scheduler owns
  in-process queue and lease state that is not yet coordinated across processes.
* Every blocking manager call runs in the threadpool; async endpoints never
  perform disk, subprocess or network work on the event loop.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from facebook_backend import __version__
from facebook_backend.api import build_router
from facebook_backend.api.responses import json_response
from facebook_backend.compatibility import legacy
from facebook_backend.config import Settings
from facebook_backend.domain.exceptions import BackendError
from facebook_backend.security.guard import TrustBoundaryMiddleware

LOGGER = logging.getLogger("facebook_backend")

ManagerFactory = Callable[[], Any]


def _build_default_manager(settings: Settings) -> Any:
    return legacy.build_manager(
        serial=settings.serial,
        base_dir=settings.base_dir,
        load_local_config=settings.load_local_config,
    )


async def _shutdown_manager(manager: Any, settings: Settings) -> None:
    """Best-effort shutdown that never fabricates a "stopped" claim.

    ``JobManager`` has no cooperative drain yet (that lands in the reliability
    phase).  If a future manager exposes ``shutdown()`` it is awaited here;
    otherwise the existing manifest/process-identity recovery path is the
    authority on the next start.  Nothing is force-killed from the API process.
    """
    shutdown = getattr(manager, "shutdown", None)
    if callable(shutdown):
        LOGGER.info("backend.shutdown.drain_start")
        await run_in_threadpool(shutdown)
        LOGGER.info("backend.shutdown.drain_done")
        return
    LOGGER.warning(
        "backend.shutdown.no_cooperative_drain",
        extra={"hint": "recovery relies on manifest + process identity checks on next start"},
    )


def create_app(
    *,
    manager: Any | None = None,
    manager_factory: ManagerFactory | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the ASGI app.

    ``manager`` lets tests (and embedding code) inject an already-built manager.
    ``manager_factory`` is used instead of the real ``JobManager`` when a test
    wants the lifespan path exercised without touching ADB.
    """
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.closing = False
        app.state.started_at = None
        started = time.monotonic()
        if app.state.manager is None:
            factory = app.state.manager_factory or (lambda: _build_default_manager(settings))
            app.state.manager = await run_in_threadpool(factory)
            app.state.manager_owned = True
        app.state.started_at = time.monotonic()
        LOGGER.info(
            "backend.startup",
            extra={
                "host": settings.host,
                "port": settings.port,
                "serial": settings.serial,
                "manager_owned": app.state.manager_owned,
                "startup_ms": round((app.state.started_at - started) * 1000, 1),
            },
        )
        try:
            yield
        finally:
            app.state.closing = True
            if app.state.manager_owned and app.state.manager is not None:
                await _shutdown_manager(app.state.manager, settings)
            LOGGER.info("backend.shutdown")

    app = FastAPI(
        title="Facebook Automation Platform",
        description=(
            "Device-aware automation task orchestration backend. "
            "Phase 1 keeps the legacy JobManager as the single state owner while "
            "the HTTP contract moves to FastAPI."
        ),
        version=__version__,
        lifespan=lifespan,
        redirect_slashes=False,
    )
    app.state.settings = settings
    app.state.manager = manager
    app.state.manager_factory = manager_factory
    # An injected manager is owned by the caller (tests, embedding code); only a
    # manager this app built during startup is shut down on the way out.
    app.state.manager_owned = False
    app.state.closing = False

    app.add_middleware(TrustBoundaryMiddleware, default_port=settings.port)
    app.include_router(build_router())

    @app.exception_handler(BackendError)
    async def _backend_error(_request, exc: BackendError):
        return json_response({"error": exc.message}, status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc: StarletteHTTPException):
        # The legacy handler answered every unmatched (method, path) pair with
        # ``404 {"error": "not found"}``; Starlette would answer 405.  Keep the
        # observable contract, and never leak Starlette's default detail shape.
        if exc.status_code in {404, 405}:
            return json_response({"error": "not found"}, status_code=404)
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return json_response({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request, exc: RequestValidationError):
        return json_response({"error": "invalid request"}, status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc: Exception):
        LOGGER.exception("backend.unhandled_error", exc_info=exc)
        return json_response({"error": "internal server error"}, status_code=500)

    return app


__all__ = ["create_app"]
