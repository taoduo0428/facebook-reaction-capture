"""Domain errors.

Two vocabularies live here on purpose:

* ``ApiError`` / ``SchedulerStartError`` / ``WorkerUnavailableError`` are the
  **operational** errors the capture service has always raised.  They moved out
  of ``facebook_capture_api`` unchanged (same classes, same inheritance) so the
  old module, the new services and the tests all agree on one type identity.
* ``BackendError`` and its subclasses are the **HTTP** errors the FastAPI layer
  raises.  ``compatibility.legacy.map_legacy_errors`` translates the first
  vocabulary into the second, and nothing else may.
"""
from __future__ import annotations


class ApiError(RuntimeError):
    """Operational failure inside the capture service (bad input, I/O, timeout)."""


class SchedulerStartError(RuntimeError):
    """Native scheduler launch failed; state must be inspected before retry."""


class WorkerUnavailableError(ApiError):
    """The selected worker could not provide a usable Android device."""


class BackendError(Exception):
    """Base class for errors that map onto a specific HTTP status."""

    status_code = 500

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class BadRequestError(BackendError):
    status_code = 400


class NotFoundError(BackendError):
    status_code = 404


class ConflictError(BackendError):
    status_code = 409


class ServiceUnavailableError(BackendError):
    status_code = 503
