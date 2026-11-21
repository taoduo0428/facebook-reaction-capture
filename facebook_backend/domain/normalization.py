"""Request normalisation (the accepted-submission contract).

Moved verbatim out of ``facebook_capture_api``: same field names, same accepted
types, same error messages.  Clients and the legacy UI depend on those messages,
so treat this module as a contract rather than an implementation detail.

Two rules worth stating explicitly:

* ``reaction_scroll_limit_explicit`` records whether the caller asked for a
  bounded sample.  ``None`` (no limit) is the historical default and must not be
  turned into a cap.
* ``normalize_persisted_request`` deliberately drops a numeric limit from older
  manifests that never stored the explicitness bit: that number may only be a
  materialised default, so it cannot be trusted as a bounded-sample request.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from facebook_backend.artifacts.catalog import DEFAULT_SAVE_ARTIFACTS, OPTIONAL_ARTIFACT_GROUPS
from facebook_backend.domain.exceptions import ApiError
from facebook_backend.domain.limits import (
    MAX_BATCH_POSTS,
    REACTION_SCROLL_LIMIT_DEFAULT,
    REACTION_SCROLL_LIMIT_MAX,
    REACTION_SCROLL_LIMIT_MIN,
)


def normalize_post_url(value: str) -> str:
    value = str(value or "").strip().replace("\\u0026", "&")
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ApiError("invalid post_url") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise ApiError("post_url must be an absolute http(s) URL")
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        raise ApiError("post_url must be a Facebook URL")
    if (
        parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80 if parsed.scheme == "http" else 443}
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ApiError("post_url contains unsupported authority or characters")
    return value


def normalize_collect(value: Any) -> list[str]:
    if value is None:
        return ["reactions"]
    if not isinstance(value, list):
        raise ApiError("collect must be a list")
    allowed = {"reactions", "shares"}
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in allowed:
            raise ApiError(f"unsupported collect item: {name or item}")
        if name not in result:
            result.append(name)
    if not result:
        raise ApiError("collect cannot be empty")
    return result


def normalize_save_artifacts(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_SAVE_ARTIFACTS)
    if not isinstance(value, list):
        raise ApiError("save_artifacts must be a list")
    result: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in OPTIONAL_ARTIFACT_GROUPS:
            raise ApiError(f"unsupported artifact group: {name or item}")
        if name not in result:
            result.append(name)
    return result


def normalize_reaction_scroll_limit(value: Any) -> int | None:
    if value is None:
        return REACTION_SCROLL_LIMIT_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(
            "reaction_scroll_limit must be an integer between "
            f"{REACTION_SCROLL_LIMIT_MIN} and {REACTION_SCROLL_LIMIT_MAX}"
        )
    if not REACTION_SCROLL_LIMIT_MIN <= value <= REACTION_SCROLL_LIMIT_MAX:
        raise ApiError(
            "reaction_scroll_limit must be an integer between "
            f"{REACTION_SCROLL_LIMIT_MIN} and {REACTION_SCROLL_LIMIT_MAX}"
        )
    return value


def normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    post_url = normalize_post_url(payload.get("post_url", ""))
    post_id = str(payload.get("post_id") or "").strip()
    if not post_url and not post_id:
        raise ApiError("post_url or post_id is required")
    if post_id and not re.fullmatch(r"[A-Za-z0-9_.:-]+", post_id):
        raise ApiError("post_id contains unsupported characters")
    reaction_scroll_limit = normalize_reaction_scroll_limit(payload.get("reaction_scroll_limit"))
    return {
        "post_url": post_url,
        "post_id": post_id,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
        "reaction_scroll_limit": reaction_scroll_limit,
        "reaction_scroll_limit_explicit": (
            "reaction_scroll_limit" in payload and reaction_scroll_limit is not None
        ),
    }


def normalize_batch_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object")
    values = payload.get("post_urls")
    if not isinstance(values, list):
        raise ApiError("post_urls must be a list")
    post_urls: list[str] = []
    for value in values:
        url = normalize_post_url(str(value or ""))
        if url and url not in post_urls:
            post_urls.append(url)
    if not post_urls:
        raise ApiError("post_urls cannot be empty")
    if len(post_urls) > MAX_BATCH_POSTS:
        raise ApiError(f"a batch can contain at most {MAX_BATCH_POSTS} unique posts")
    reaction_scroll_limit = normalize_reaction_scroll_limit(payload.get("reaction_scroll_limit"))
    return {
        "post_urls": post_urls,
        "collect": normalize_collect(payload.get("collect")),
        "save_artifacts": normalize_save_artifacts(payload.get("save_artifacts")),
        "reaction_scroll_limit": reaction_scroll_limit,
        "reaction_scroll_limit_explicit": (
            "reaction_scroll_limit" in payload and reaction_scroll_limit is not None
        ),
    }


def normalize_persisted_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a job manifest while preserving the old unlimited default.

    The first pagination-limit manifests did not persist an explicitness bit,
    so a stored numeric value may only be a materialized default.  Treat those
    manifests as unlimited; only a manifest with an explicit true marker can
    restore a bounded sample.
    """
    if not isinstance(payload, dict):
        raise ApiError("persisted request must be an object")
    normalized_payload = dict(payload)
    if normalized_payload.get("reaction_scroll_limit_explicit") is not True:
        normalized_payload.pop("reaction_scroll_limit", None)
        normalized_payload.pop("reaction_scroll_limit_explicit", None)
    request = normalize_request(normalized_payload)
    if normalized_payload.get("reaction_scroll_limit_explicit") is not True:
        request["reaction_scroll_limit"] = REACTION_SCROLL_LIMIT_DEFAULT
        request["reaction_scroll_limit_explicit"] = False
    return request


__all__ = [
    "normalize_batch_request",
    "normalize_collect",
    "normalize_persisted_request",
    "normalize_post_url",
    "normalize_reaction_scroll_limit",
    "normalize_request",
    "normalize_save_artifacts",
]
