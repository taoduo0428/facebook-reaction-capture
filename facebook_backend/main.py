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

import inspect
import logging
import threading
import time
from contextlib import ExitStack, asynccontextmanager
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
from facebook_backend.observability.logging import configure_logging, get_logger, log_event
from facebook_backend.observability.middleware import ObservabilityMiddleware
from facebook_backend.persistence.state_startup import open_state_store as _open_state_store, state_owner
from facebook_backend.security.guard import TrustBoundaryMiddleware

LOGGER = get_logger("app")

ManagerFactory = Callable[[], Any]


def _close_state_after_worker(manager: Any, resources: ExitStack) -> None:
    """Retain the database and both locks until a timed-out worker exits."""
    worker = getattr(manager, "_worker", None)
    if worker is None or not worker.is_alive():
        resources.close()
        return

    def wait_and_close() -> None:
        worker.join()
        resources.close()

    threading.Thread(target=wait_and_close, name="state-owner-release", daemon=True).start()
    log_event(LOGGER, "backend.shutdown.owner_retained", level=logging.WARNING)


def _build_default_manager(settings: Settings, store: Any | None = None) -> Any:
    return legacy.build_manager(
        serial=settings.serial,
        base_dir=settings.base_dir,
        load_local_config=settings.load_local_config,
        max_runtime_seconds=settings.max_runtime_seconds,
        store=store,
    )


async def _shutdown_manager(manager: Any, settings: Settings) -> None:
    """Bounded drain that never fabricates a "stopped" claim.

    ``JobManager.shutdown(wait_seconds=...)`` stops accepting submissions and
    waits for the running job; it does not kill the capture process and does not
    mark anything stopped.  If the wait expires, the process exits and the next
    start verifies the old capture through process identity — the same authority
    the legacy restart path always used.  A manager without ``shutdown`` (older
    embedders) is left to that same recovery path.
    """
    shutdown = getattr(manager, "shutdown", None)
    if callable(shutdown):
        log_event(
            LOGGER, "backend.shutdown.drain_start", wait_seconds=settings.shutdown_wait_seconds
        )
        try:
            inspect.signature(shutdown).bind(wait_seconds=settings.shutdown_wait_seconds)
        except TypeError:
            kwargs = {}  # An older embedder with a parameterless shutdown.
        else:
            kwargs = {"wait_seconds": settings.shutdown_wait_seconds}
        result = await run_in_threadpool(shutdown, **kwargs)
        log_event(LOGGER, "backend.shutdown.drain_done", drain=result)
        return
    log_event(
        LOGGER,
        "backend.shutdown.no_cooperative_drain",
        level=logging.WARNING,
        hint="recovery relies on manifest + process identity checks on next start",
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

    lifespan_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not lifespan_lock.acquire(blocking=False):
            raise RuntimeError("application lifespan already active")
        resources = ExitStack()
        owned = None
        try:
            configure_logging(settings.log_level)
            app.state.closing = False
            started = time.monotonic()
            if manager is None:
                if manager_factory is not None:
                    owned = await run_in_threadpool(manager_factory)
                else:
                    lock_url = None if settings.job_store.strip().lower() == "manifest" else settings.database_url
                    await run_in_threadpool(resources.enter_context, state_owner(settings.base_dir, lock_url))
                    store, database = await run_in_threadpool(_open_state_store, settings)
                    if database is not None:
                        resources.callback(database.dispose)
                    app.state.database = database
                    owned = await run_in_threadpool(_build_default_manager, settings, store)
                app.state.manager = owned
                app.state.manager_owned = True
            app.state.started_at = time.monotonic()
            log_event(
                LOGGER, "backend.startup", host=settings.host, port=settings.port,
                serial=settings.serial, manager_owned=app.state.manager_owned,
                startup_ms=round((app.state.started_at - started) * 1000, 1),
            )
            yield
        finally:
            app.state.closing = True
            try:
                if owned is not None:
                    await _shutdown_manager(owned, settings)
            finally:
                try:
                    if manager_factory is None:
                        await run_in_threadpool(_close_state_after_worker, owned, resources)
                    else:
                        resources.close()
                finally:
                    app.state.manager = manager
                    app.state.manager_owned = False
                    app.state.database = None
                    app.state.started_at = None
                    lifespan_lock.release()
                    log_event(LOGGER, "backend.shutdown")

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
    # Added last so it is outermost: rejected requests are still observed.
    app.add_middleware(ObservabilityMiddleware)
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
        LOGGER.exception("backend.unhandled_error")
        return json_response({"error": "internal server error"}, status_code=500)

    return app


__all__ = ["create_app"]
