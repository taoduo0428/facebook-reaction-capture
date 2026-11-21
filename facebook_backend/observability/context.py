"""Request/job context carried through logs and traces.

Every log line and span in this codebase is expected to answer "which job, which
batch, which worker, which device, which attempt, which trace".  Rather than
threading six parameters through every function, the fields live in a
``ContextVar`` that the API middleware and the scheduler bind once per unit of
work.

``ContextVar`` (not a global dict) matters here: the scheduler runs jobs in
worker threads, and each thread must see its own context.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

#: Canonical field order used by the JSON log formatter and the span attributes.
CONTEXT_FIELDS = (
    "job_id",
    "batch_id",
    "worker_id",
    "device_serial",
    "attempt_id",
    "trace_id",
    "request_id",
)

_context: ContextVar[dict[str, str]] = ContextVar("facebook_observability_context", default={})


def bind(**fields: Any) -> Token:
    """Merge fields into the current context; returns a token for :func:`reset`."""
    merged = dict(_context.get())
    for key, value in fields.items():
        if value is None or value == "":
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    return _context.set(merged)


def reset(token: Token) -> None:
    _context.reset(token)


def current() -> dict[str, str]:
    return dict(_context.get())


def snapshot(*, trace_id: str | None = None) -> dict[str, str]:
    """Current context with an optional trace id override (from the live span)."""
    fields = current()
    if trace_id:
        fields["trace_id"] = trace_id
    return {key: value for key, value in fields.items() if value}


@contextmanager
def bound(**fields: Any) -> Iterator[dict[str, str]]:
    token = bind(**fields)
    try:
        yield current()
    finally:
        reset(token)


__all__ = ["CONTEXT_FIELDS", "bind", "bound", "current", "reset", "snapshot"]
