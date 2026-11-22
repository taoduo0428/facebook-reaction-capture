"""Failure classification and retry policy.

The legacy worker loop distinguishes one case explicitly — ``"rate-limit" in
str(exc).lower()`` starts a cooldown instead of failing the device — and treats
everything else as a candidate for failover after re-probing ADB.  This module
makes that classification explicit, because the task forbids mixing them:

* **rate-limit** — the platform throttled us.  Cool the *device* down and keep
  the job queued for the *same* device; never switch account or device to keep
  going.
* **device** — the Android target is gone/offline.  Failover to another device
  is allowed after the failure is confirmed.
* **network** — the host lost connectivity.  Retry with backoff on the same
  device; do not blame the device.
* **executor** — the runner crashed (non-zero exit, missing artifacts).  Retry
  is allowed only if the process identity is verified stopped.
* **timeout** — the capture exceeded its budget and was terminated.
* **cancelled** — the operator asked; never retried automatically.
* **unknown** — no automatic retry; a human decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureClass(str, Enum):
    RATE_LIMIT = "rate-limit"
    DEVICE = "device"
    NETWORK = "network"
    EXECUTOR = "executor"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


RATE_LIMIT_MARKERS = ("rate limit", "rate-limit", "ratelimit", "too many requests", "429")
DEVICE_MARKERS = ("device offline", "device not found", "unauthorized", "no devices", "adb")
NETWORK_MARKERS = ("connection", "timed out", "timeout", "network", "proxy", "dns", "ssl")
TIMEOUT_MARKERS = ("capture timed out", "timed out after")
CANCEL_MARKERS = ("cancel", "stopped by operator")


def classify_failure(error: BaseException | str | None) -> FailureClass:
    message = (str(error) if error is not None else "").lower()
    if any(marker in message for marker in CANCEL_MARKERS):
        return FailureClass.CANCELLED
    if any(marker in message for marker in TIMEOUT_MARKERS):
        return FailureClass.TIMEOUT
    if any(marker in message for marker in RATE_LIMIT_MARKERS):
        return FailureClass.RATE_LIMIT
    if any(marker in message for marker in DEVICE_MARKERS):
        return FailureClass.DEVICE
    if any(marker in message for marker in NETWORK_MARKERS):
        return FailureClass.NETWORK
    if error is not None:
        return FailureClass.EXECUTOR
    return FailureClass.UNKNOWN


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    same_device: bool
    cooldown_seconds: int
    failover_allowed: bool
    reason: str


@dataclass(frozen=True)
class RetryPolicy:
    """Explicit, testable retry rules (no hidden magic in the scheduler)."""

    max_attempts: int = 3
    rate_limit_cooldown_seconds: int = 900
    network_backoff_seconds: tuple[int, ...] = (30, 120, 300)

    def decide(
        self,
        failure: FailureClass,
        *,
        attempt: int,
        device_cooldown_seconds: int = 900,
    ) -> RetryDecision:
        if failure is FailureClass.CANCELLED:
            return RetryDecision(False, False, 0, False, "cancelled jobs are never retried")
        if failure is FailureClass.RATE_LIMIT:
            # Keep the job on the same device: switching identity to dodge a
            # rate limit is exactly what the task forbids.
            return RetryDecision(
                True,
                True,
                max(self.rate_limit_cooldown_seconds, device_cooldown_seconds),
                False,
                "rate limited: cool the device down and keep the job on it",
            )
        if failure is FailureClass.NETWORK:
            backoff = self.network_backoff_seconds[min(attempt, len(self.network_backoff_seconds) - 1)]
            return RetryDecision(True, True, backoff, False, "network failure: retry with backoff")
        if failure is FailureClass.DEVICE:
            if attempt >= self.max_attempts:
                return RetryDecision(False, False, 0, False, "device failure: attempts exhausted")
            return RetryDecision(
                True, False, device_cooldown_seconds, True, "device failure: failover allowed"
            )
        if failure is FailureClass.TIMEOUT:
            return RetryDecision(
                False, False, 0, False, "timeout: require a verified stop before any retry"
            )
        if failure is FailureClass.EXECUTOR:
            if attempt >= self.max_attempts:
                return RetryDecision(False, False, 0, False, "executor failure: attempts exhausted")
            return RetryDecision(
                True, False, device_cooldown_seconds, True, "executor failure: retry after verified stop"
            )
        return RetryDecision(False, False, 0, False, "unknown failure: no automatic retry")


__all__ = [
    "FailureClass",
    "RetryDecision",
    "RetryPolicy",
    "classify_failure",
]
