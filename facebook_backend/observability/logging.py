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
import re
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
    "submission_key",
    "idempotency",
    "url",
    "uri",
    "headers",
    "payload",
    "body",
    "error",
    "exception",
    "message",
    "detail",
    "reason",
    "query",
    "path",
    "baggage",
    "tracestate",
    "traceparent",
    "auth",
    "bearer",
    "accesskey",
    "sessionid",
    "cursor",
)

_SYMBOL = re.compile(r"[a-zA-Z0-9_.:-]{1,128}\Z")
_FIELD = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}\Z")
_ROUTE = re.compile(r"/[a-zA-Z0-9_./{}:-]{0,160}\Z")


def _is_secret(key: str) -> bool:
    compact = key.lower().replace("-", "").replace("_", "").replace(".", "")
    return any(marker.replace("_", "") in compact for marker in SECRET_MARKERS)


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    """Export scalar dimensions only. Never stringify a body, object or exception."""
    clean = {}
    for key, value in fields.items():
        if type(key) is not str or not _FIELD.fullmatch(key) or _is_secret(key):
            continue
        if type(value) in (bool, int, float):
            clean[key] = value
        elif type(value) is str and (
            _SYMBOL.fullmatch(value) or (key in {"http_route", "route"} and _ROUTE.fullmatch(value))
        ):
            clean[key] = value
    return clean


def _event(record: logging.LogRecord) -> str:
    # A printf argument or free-form exception message is not an event name.
    if not record.args and type(record.msg) is str and _SYMBOL.fullmatch(record.msg):
        return record.msg
    return "log.message"


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.levelname} {_event(record)}"


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
            "event": _event(record),
        }
        payload.update(redact(snapshot()))
        structured = getattr(record, "fields", None)
        if isinstance(structured, dict):
            payload.update(redact(structured))
        if record.exc_info:
            payload["exception"] = True
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
        JsonFormatter(service=service) if json_format else _TextFormatter()
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
