"""HTTP layer: routers and response helpers."""
from __future__ import annotations

__all__ = ["build_router"]


def build_router():
    """Return one APIRouter with every legacy route registered in order.

    Route order matters: ``/v1/jobs/latest`` must be declared before
    ``/v1/jobs/{job_id}`` exactly like the legacy ``if`` chain.
    """
    from fastapi import APIRouter

    from facebook_backend.api.routes import avatars, batches, health, jobs, setup, static, workers

    router = APIRouter()
    router.include_router(setup.router)
    router.include_router(static.router)
    router.include_router(health.router)
    router.include_router(jobs.router)
    router.include_router(batches.router)
    router.include_router(workers.router)
    router.include_router(avatars.router)
    return router
