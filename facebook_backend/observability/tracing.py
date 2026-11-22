"""OpenTelemetry tracing with a no-op fallback.

The tracer is held locally instead of being pushed into the global
``trace.set_tracer_provider`` slot on purpose: tests create several providers in
one process, and the global setter is write-once.  Spans still participate in the
OpenTelemetry context (``start_as_current_span``), so ``current_trace_id()`` and
nested spans work, and a real deployment can opt into the global provider with
``set_global=True``.

If the SDK is not installed, ``get_tracer()`` returns a no-op tracer: logging and
metrics keep working, and nothing in the request path has to guard for it.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Iterator

from facebook_backend.observability.context import bound, snapshot
from facebook_backend.observability.logging import redact

SERVICE_NAME = "facebook-automation-platform"


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401 - no-op
        return None

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None

    def is_recording(self) -> bool:
        return False

    def get_span_context(self) -> Any:
        return SimpleNamespace(trace_id=0, span_id=0)


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, attributes: dict[str, Any] | None = None, **_: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


_tracer: Any = _NoopTracer()
_provider: Any = None
_sdk_available: bool | None = None
_entry_tracer: ContextVar[Any] = ContextVar("facebook_entry_tracer", default=None)
_INHERIT = object()


@lru_cache(maxsize=1)
def _local_tracer() -> Any:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    # No exporter, network IO, shutdown hook or environment resource detectors.
    provider = TracerProvider(resource=Resource({"service.name": SERVICE_NAME}), shutdown_on_exit=False)
    return provider.get_tracer(SERVICE_NAME)


def ensure_tracing() -> Any:
    """Choose a tracer for an HTTP/worker entry, leaving embedded providers intact."""
    if _provider is not None or not sdk_available():
        return _tracer
    return _local_tracer()


def sdk_available() -> bool:
    global _sdk_available
    if _sdk_available is None:
        try:
            import opentelemetry.sdk.trace  # noqa: F401
        except ImportError:
            _sdk_available = False
        else:
            _sdk_available = True
    return _sdk_available


def configure_tracing(
    *,
    service_name: str = SERVICE_NAME,
    exporter: Any = None,
    sample_ratio: float = 1.0,
    resource_attributes: dict[str, str] | None = None,
    set_global: bool = False,
) -> Any | None:
    """Create a tracer provider; returns ``None`` when the SDK is unavailable."""
    global _tracer, _provider
    if not sdk_available():
        _tracer = _NoopTracer()
        _provider = None
        return None
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    attributes = {"service.name": service_name}
    attributes.update(redact(resource_attributes or {}))
    provider = TracerProvider(
        resource=Resource(attributes),
        sampler=ParentBased(TraceIdRatioBased(max(0.0, min(1.0, sample_ratio)))),
    )
    if exporter is not None:
        processor = (
            BatchSpanProcessor(exporter)
            if getattr(exporter, "force_flush", None) is not None
            and not hasattr(exporter, "get_finished_spans")
            else SimpleSpanProcessor(exporter)
        )
        provider.add_span_processor(processor)
    if set_global:
        from opentelemetry import trace

        trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = provider.get_tracer(service_name)
    return provider


def get_tracer() -> Any:
    return _entry_tracer.get() or _tracer


def get_provider() -> Any:
    return _provider


def reset_tracing() -> None:
    """Test helper: back to the no-op tracer."""
    global _tracer, _provider
    _tracer = _NoopTracer()
    _provider = None


def current_trace_id() -> str | None:
    """The active trace id as 32 hex chars, or ``None`` outside a span."""
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
    except Exception:  # noqa: BLE001 - tracing must never break a request
        return None
    trace_id = getattr(context, "trace_id", 0)
    if not trace_id:
        return None
    return format(trace_id, "032x")


_STORED_PARENT = re.compile(r"00-(?!0{32}-)([0-9a-f]{32})-(?!0{16}-)[0-9a-f]{16}-[0-9a-f]{2}")


def trace_id_from_traceparent(value: Any) -> str | None:
    """Read the trace ID from an internally persisted W3C v00 parent, if valid."""
    match = _STORED_PARENT.fullmatch(value) if isinstance(value, str) and len(value) == 55 else None
    return match.group(1) if match else None


def current_traceparent() -> str | None:
    """Return only the standard W3C parent, suitable for private job metadata."""
    if isinstance(get_tracer(), _NoopTracer):
        return None
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


@contextmanager
def span(name: str, *, attributes: dict[str, Any] | None = None, **fields: Any) -> Iterator[Any]:
    """Start a child of the current span and bind its trace ID for the whole body."""
    with _span(name, _INHERIT, {**(attributes or {}), **fields}) as active:
        yield active


@contextmanager
def _span(name: str, parent: Any, fields: dict[str, Any]) -> Iterator[Any]:
    attrs = redact({**snapshot(), **fields})
    kwargs = {} if parent is _INHERIT else {"context": parent}
    # The SDK's default exception event/status includes str(exc) and a traceback.
    with get_tracer().start_as_current_span(name, attributes=attrs, record_exception=False,
                                             set_status_on_exception=False, **kwargs) as active:
        trace_id = None if isinstance(active, _NoopSpan) else current_trace_id()
        if trace_id:
            active.set_attribute("trace_id", trace_id)
        with bound(trace_id=trace_id):
            try:
                yield active
            except BaseException:
                if active.is_recording():
                    from opentelemetry.trace import StatusCode

                    active.set_status(StatusCode.ERROR)
                raise


@contextmanager
def entry_span(name: str, traceparent: str | None, **fields: Any) -> Iterator[Any]:
    """Enter an isolated HTTP/worker unit. Only traceparent may cross the boundary."""
    tracer = ensure_tracing()
    token = _entry_tracer.set(tracer)
    try:
        parent = None
        if not isinstance(tracer, _NoopTracer):
            from opentelemetry.context import Context
            from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

            # Start at an empty context, including when a header is missing/invalid.
            parent = Context()
            if isinstance(traceparent, str) and len(traceparent) == 55:
                parent = TraceContextTextMapPropagator().extract({"traceparent": traceparent}, context=parent)
        with _span(name, parent, fields) as active:
            yield active
    finally:
        _entry_tracer.reset(token)


@contextmanager
def execution_context(traceparent: str | None, *, job_id: str, batch_id: str | None = None,
                      worker_id: str | None = None, device_serial: str | None = None,
                      attempt_id: str | int | None = None, request_id: str | None = None) -> Iterator[Any]:
    """Restore the submission parent in the thread that owns this attempt."""
    with bound(job_id=job_id, batch_id=batch_id, worker_id=worker_id, device_serial=device_serial,
               attempt_id=attempt_id, request_id=request_id, trace_id=None):
        with entry_span("job.execute", traceparent) as active:
            yield active


__all__ = [
    "SERVICE_NAME",
    "configure_tracing",
    "current_trace_id",
    "current_traceparent",
    "ensure_tracing",
    "entry_span",
    "execution_context",
    "get_provider",
    "get_tracer",
    "reset_tracing",
    "sdk_available",
    "span",
    "trace_id_from_traceparent",
]
