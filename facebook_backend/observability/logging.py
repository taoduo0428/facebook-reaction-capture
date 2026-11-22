"""Structured logging.

One JSON object per line, always carrying the context fields (job/batch/worker/
device/attempt/trace).  Two rules are enforced here rather than left to habit:

* **No secrets.**  Keys whose name looks like a credential are dropped before
  serialisation, and request bodies are never logged at all (the request log
  records method, route, status and duration only).
* **No free-form payloads.**  ``log_event(logger, "job.completed", job_id=...)``
  produces ``{"event": "job.completed", ...}`` — the event name is a stable
  identifier a dashboard can count, not a sentence.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, IO

from facebook_backend.observability.context import snapshot

LOGGER_NAME = "facebook_backend"

#: Substrings that mark a field as a credential; such fields are never logged.
SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
)


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if not _is_secret(str(key))}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object with the observability fields."""

    def __init__(self, *, service: str = "facebook-backend"):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "event": record.getMessage(),
        }
        payload.update(snapshot())
        structured = getattr(record, "fields", None)
        if isinstance(structured, dict):
            payload.update(redact(structured))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    level: str = "INFO",
    *,
    stream: IO[str] | None = None,
    service: str = "facebook-backend",
    json_format: bool = True,
    force: bool = False,
) -> logging.Logger:
    """Install the structured handler on the backend logger.

    Idempotent by default: if a structured handler is already installed (a test
    capture, or an embedding application), only the level is updated.  Otherwise
    the lifespan would silently replace the caller's handler on every startup.
    ``force=True`` replaces it explicitly (used by test captures).
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    existing = [h for h in logger.handlers if getattr(h, "_facebook_structured", False)]
    if existing and not force:
        logger.propagate = False
        return logger
    for handler in existing:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        JsonFormatter(service=service) if json_format else logging.Formatter("%(levelname)s %(message)s")
    )
    handler._facebook_structured = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event; the message is the stable event name."""
    logger.log(level, event, extra={"fields": redact(fields)})


__all__ = [
    "LOGGER_NAME",
    "SECRET_MARKERS",
    "JsonFormatter",
    "configure_logging",
    "get_logger",
    "log_event",
    "redact",
]
