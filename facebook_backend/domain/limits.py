"""Numeric limits shared by the HTTP layer and the request normaliser.

Values are unchanged from ``facebook_capture_api``; they moved so the FastAPI
schemas and the legacy normaliser quote the same numbers instead of drifting.
"""
from __future__ import annotations

MAX_BATCH_POSTS = 100

REACTION_SCROLL_LIMIT_MIN = 1
REACTION_SCROLL_LIMIT_MAX = 300
# ``None`` means the API caller did not request a bounded sample.  The capture
# command then keeps the existing unlimited/terminal pagination path.
REACTION_SCROLL_LIMIT_DEFAULT: int | None = None

__all__ = [
    "MAX_BATCH_POSTS",
    "REACTION_SCROLL_LIMIT_DEFAULT",
    "REACTION_SCROLL_LIMIT_MAX",
    "REACTION_SCROLL_LIMIT_MIN",
]
