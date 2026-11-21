"""Domain-level errors and status vocabularies.

The HTTP layer never raises ``facebook_capture_api.ApiError`` directly: legacy
manager errors are translated here so routers only speak one error language.
"""
from __future__ import annotations


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
