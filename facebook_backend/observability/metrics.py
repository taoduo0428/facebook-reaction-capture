"""Metrics registry with Prometheus text exposition.

Self-contained on purpose: the task says Prometheus/Grafana come later, but the
values they need (latency, queue depth, retries, failovers, recoveries) have to
be recorded *now* or the history is lost.  Rendering the standard text format
means a real Prometheus can scrape ``/metrics`` without adding a dependency;
swapping in ``prometheus_client`` later is a drop-in change.

Histogram percentiles are computed from bucket boundaries, so they are upper
bounds — documented rather than pretended to be exact.  Counts and sums are
exact.
"""
from __future__ import annotations

import threading
from bisect import bisect_left
from typing import Any, Iterable, Mapping

from facebook_backend.observability.logging import redact

LabelKey = tuple[tuple[str, str], ...]

DEFAULT_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
    10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0,
)


def _key(labels: Mapping[str, Any] | None) -> LabelKey:
    if not labels:
        return ()
    return tuple(sorted((k, str(v)) for k, v in redact(dict(labels)).items()))


def _render_labels(key: LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{value}"' for name, value in key)
    return "{" + inner + "}"


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._lock = threading.Lock()

    def _samples(self) -> Iterable[tuple[LabelKey, float]]:  # pragma: no cover - overridden
        return ()

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]
        for key, value in self._samples():
            lines.append(f"{self.name}{_render_labels(key)} {value}")
        return lines


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._values: dict[LabelKey, float] = {}

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        key = _key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def value(self, **labels: Any) -> float:
        return self._values.get(_key(labels), 0.0)

    def _samples(self) -> Iterable[tuple[LabelKey, float]]:
        with self._lock:
            return list(self._values.items())


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._values: dict[LabelKey, float] = {}

    def set(self, value: float, **labels: Any) -> None:
        with self._lock:
            self._values[_key(labels)] = float(value)

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        key = _key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def dec(self, value: float = 1.0, **labels: Any) -> None:
        self.inc(-value, **labels)

    def value(self, **labels: Any) -> float:
        return self._values.get(_key(labels), 0.0)

    def _samples(self) -> Iterable[tuple[LabelKey, float]]:
        with self._lock:
            return list(self._values.items())


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, *, buckets: Iterable[float] = DEFAULT_BUCKETS):
        super().__init__(name, help_text)
        self.buckets = tuple(sorted(float(b) for b in buckets))
        self._counts: dict[LabelKey, list[int]] = {}
        self._sum: dict[LabelKey, float] = {}
        self._count: dict[LabelKey, int] = {}
        self._max: dict[LabelKey, float] = {}

    def observe(self, value: float, **labels: Any) -> None:
        key = _key(labels)
        value = float(value)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            index = bisect_left(self.buckets, value)
            if index < len(self.buckets):
                counts[index] += 1
            self._sum[key] = self._sum.get(key, 0.0) + value
            self._count[key] = self._count.get(key, 0) + 1
            self._max[key] = max(self._max.get(key, value), value)

    def snapshot(self, **labels: Any) -> dict[str, Any]:
        key = _key(labels)
        with self._lock:
            counts = list(self._counts.get(key, [0] * len(self.buckets)))
            total = self._count.get(key, 0)
            total_sum = self._sum.get(key, 0.0)
            maximum = self._max.get(key, 0.0)
        cumulative: list[int] = []
        running = 0
        for count in counts:
            running += count
            cumulative.append(running)
        return {
            "count": total,
            "sum": total_sum,
            "max": maximum,
            "buckets": list(zip(self.buckets, cumulative)),
            "p50": self._percentile(cumulative, total, 0.50, maximum),
            "p95": self._percentile(cumulative, total, 0.95, maximum),
            "p99": self._percentile(cumulative, total, 0.99, maximum),
        }

    def _percentile(self, cumulative: list[int], total: int, quantile: float, maximum: float) -> float:
        if total == 0:
            return 0.0
        target = quantile * total
        for boundary, count in zip(self.buckets, cumulative):
            if count >= target:
                return boundary
        return maximum

    def _samples(self) -> Iterable[tuple[LabelKey, float]]:
        lines: list[tuple[LabelKey, float]] = []
        with self._lock:
            keys = list(self._count.keys())
        for key in keys:
            with self._lock:
                counts = list(self._counts.get(key, []))
                total = self._count.get(key, 0)
                total_sum = self._sum.get(key, 0.0)
            running = 0
            for boundary, count in zip(self.buckets, counts):
                running += count
                lines.append(
                    (key + (("le", _fmt(boundary)), ("__suffix__", "bucket")), float(running))
                )
            lines.append((key + (("le", "+Inf"), ("__suffix__", "bucket")), float(total)))
            lines.append((key + (("__suffix__", "sum"),), total_sum))
            lines.append((key + (("__suffix__", "count"),), float(total)))
        return lines

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key, value in self._samples():
            suffix = ""
            labels = key
            if key and key[-1][0] == "__suffix__":
                suffix = "_" + key[-1][1]
                labels = key[:-1]
            lines.append(f"{self.name}{suffix}{_render_labels(labels)} {_fmt(value)}")
        return lines


def _fmt(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str) -> Counter:
        return self._get(name, lambda: Counter(name, help_text))

    def gauge(self, name: str, help_text: str) -> Gauge:
        return self._get(name, lambda: Gauge(name, help_text))

    def histogram(
        self, name: str, help_text: str, *, buckets: Iterable[float] = DEFAULT_BUCKETS
    ) -> Histogram:
        return self._get(name, lambda: Histogram(name, help_text, buckets=buckets))

    def _get(self, name: str, factory) -> Any:
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                metric = factory()
                self._metrics[name] = metric
            return metric

    def render(self) -> str:
        with self._lock:
            metrics = sorted(self._metrics.values(), key=lambda metric: metric.name)
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metrics)

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()


class PlatformMetrics:
    """The metrics this platform actually emits (no decorative counters)."""

    def __init__(self, registry: MetricsRegistry | None = None):
        self.registry = registry or MetricsRegistry()
        self._build()

    def _build(self) -> None:
        self.api_requests = self.registry.counter(
            "facebook_api_requests_total", "HTTP requests by method, route and status"
        )
        self.api_latency = self.registry.histogram(
            "facebook_api_request_duration_seconds", "HTTP request duration"
        )
        self.jobs_submitted = self.registry.counter(
            "facebook_jobs_submitted_total", "Accepted POST /v1/jobs requests, including idempotent replays"
        )
        self.jobs_created = self.registry.counter(
            "facebook_jobs_created_total", "New jobs durably accepted, including batch children"
        )
        self.jobs_completed = self.registry.counter(
            "facebook_jobs_completed_total", "Jobs that reached a terminal status"
        )
        self.job_execution = self.registry.histogram(
            "facebook_job_execution_seconds", "Wall time from dispatch to outcome"
        )
        self.queue_wait = self.registry.histogram(
            "facebook_queue_wait_seconds", "Time between submission and dispatch"
        )
        self.queue_depth = self.registry.gauge(
            "facebook_queue_depth", "Queued jobs waiting for a device"
        )
        self.devices = self.registry.gauge(
            "facebook_devices", "Devices by state (available, busy, cooldown, disabled, unhealthy)"
        )
        self.retries = self.registry.counter(
            "facebook_job_retries_total", "Retries scheduled by failure class"
        )
        self.failovers = self.registry.counter(
            "facebook_failovers_total", "Jobs re-dispatched to another device"
        )
        self.cancellations = self.registry.counter(
            "facebook_cancellations_total", "Cancellation requests by outcome"
        )
        self.recoveries = self.registry.counter(
            "facebook_recoveries_total", "Startup recovery actions by action"
        )
        self.artifact_verifications = self.registry.counter(
            "facebook_artifact_verifications_total", "Artifact verification results"
        )
        self.scheduler_ticks = self.registry.counter(
            "facebook_scheduler_ticks_total", "Scheduler ticks"
        )

    def render(self) -> str:
        return self.registry.render()

    def reset(self) -> None:
        """Test helper: drop every recorded value and rebuild the metric set."""
        self.registry.reset()
        self._build()


#: Process-wide registry used by the API and the scheduler.
METRICS = PlatformMetrics()


__all__ = [
    "DEFAULT_BUCKETS",
    "METRICS",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "PlatformMetrics",
]
