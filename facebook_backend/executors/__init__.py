"""Executors: the only layer that actually starts work.

* ``base`` — the ``ExecutorPort`` contract and ``ExecutionOutcome``.
* ``mock`` — scripted executor used by the scheduler tests (no device needed).
* ``legacy`` — adapter to the existing, validated ``run_capture_job`` path.
"""
from __future__ import annotations

from facebook_backend.executors import base, legacy, mock
from facebook_backend.executors.base import ExecutionOutcome, ExecutorError, ExecutorPort
from facebook_backend.executors.legacy import LegacyCaptureExecutor
from facebook_backend.executors.mock import MockExecutor

__all__ = [
    "ExecutionOutcome",
    "ExecutorError",
    "ExecutorPort",
    "LegacyCaptureExecutor",
    "MockExecutor",
    "base",
    "legacy",
    "mock",
]
