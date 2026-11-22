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

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

from facebook_backend.observability.context import snapshot

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
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    attributes = {"service.name": service_name}
    attributes.update(resource_attributes or {})
    provider = TracerProvider(
        resource=Resource.create(attributes),
        sampler=TraceIdRatioBased(max(0.0, min(1.0, sample_ratio))),
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
    return _tracer


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


@contextmanager
def span(name: str, *, attributes: dict[str, Any] | None = None, **fields: Any) -> Iterator[Any]:
    """Start a span carrying the current observability context as attributes."""
    attributes = {key: str(value) for key, value in (attributes or {}).items() if value is not None}
    attributes.update({key: str(value) for key, value in fields.items() if value is not None})
    for key, value in snapshot().items():
        attributes.setdefault(key, value)
    with get_tracer().start_as_current_span(name, attributes=attributes) as active:
        trace_id = current_trace_id()
        if trace_id:
            # Set after start: the id only exists once the span is active.
            try:
                active.set_attribute("trace_id", trace_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            yield active
        except BaseException as exc:
            try:
                active.record_exception(exc)
            except Exception:  # noqa: BLE001
                pass
            raise


__all__ = [
    "SERVICE_NAME",
    "configure_tracing",
    "current_trace_id",
    "get_provider",
    "get_tracer",
    "reset_tracing",
    "sdk_available",
    "span",
]
