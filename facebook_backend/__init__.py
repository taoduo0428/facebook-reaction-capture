"""FastAPI backend package for the Facebook capture automation platform.

Importing this package must stay side-effect free: no ``JobManager`` is built,
no scheduler thread is started and no Uvicorn server is bound until
``create_app()`` is called from an application entrypoint.

The single supported factory is :func:`create_app` in ``facebook_backend.main``.
"""
from __future__ import annotations

__all__ = ["__version__", "create_app"]

__version__ = "1.0.1"


def create_app(*args, **kwargs):
    """Lazily delegate to :func:`facebook_backend.main.create_app`."""
    from facebook_backend.main import create_app as _create_app

    return _create_app(*args, **kwargs)
