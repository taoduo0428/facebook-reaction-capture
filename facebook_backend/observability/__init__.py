"""Observability: context, structured logging, tracing, metrics.

Import the submodules explicitly; this package intentionally keeps its
``__init__`` free of side effects (no handler installation, no tracer provider).
"""
from __future__ import annotations

from facebook_backend.observability import context, logging, metrics, tracing

__all__ = ["context", "logging", "metrics", "tracing"]
