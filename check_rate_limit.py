#!/usr/bin/env python3
"""Single-request probe: is the account still throttled by Facebook?

Fires exactly ONE FeedbackReactorsGraphService request (page 1, 25 rows) and
reports whether the answer carries a throttle code.  It never pages, never
retries, and never writes capture files, so it is safe to repeat with a long
gap between runs.

Usage:
    python check_rate_limit.py --mitm runs/live-auto/raw-facebook-http.mitm
    python check_rate_limit.py --mitm <capture> --template-json worker-bootstrap/graphql-templates.json
    python check_rate_limit.py --mitm <capture> --post-id 1691321539667513

Exit codes:
    0  clear      - the account can page again
    3  throttled  - still limited, wait longer
    4  blocked    - account-level block, not a throttle
    2  error      - capture/template/network problem, nothing concluded
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from facebook_graphql_direct import (
    BlockedError,
    GraphQLClient,
    RateLimitedError,
    RATE_LIMIT_CODES,
    collect_templates,
    find_page_info,
    is_blocked,
    is_rate_limited,
    REACTOR_FIELDS,
)

DEFAULT_POST_ID = "1691321539667513"
FRIENDLY = "FeedbackReactorsGraphService"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mitm", type=Path, required=True, help="capture carrying an OAuth token")
    parser.add_argument("--template-json", type=Path, help="persisted template file")
    parser.add_argument("--post-id", default=DEFAULT_POST_ID)
    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="pages to probe before declaring clear; Facebook can serve page 1 "
        "and throttle page 2, so a single page gives false positives",
    )
    parser.add_argument("--proxy", help="upstream proxy, e.g. http://127.0.0.1:7897")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    templates, token = collect_templates(args.mitm)
    if args.template_json and args.template_json.exists():
        import json

        persisted = json.loads(args.template_json.read_text(encoding="utf-8"))
        merged = dict(persisted)
        merged.update(templates)
        templates = merged

    template = templates.get(FRIENDLY)
    if not template:
        print(f"no {FRIENDLY} template in the capture", file=sys.stderr)
        return 2
    if not token:
        print("no OAuth token in the capture", file=sys.stderr)
        return 2

    variables = dict(template.get("variables") or {})
    import base64

    variables["feedback_id"] = base64.b64encode(
        f"feedback:{args.post_id}".encode()
    ).decode()

    # Without the pagination template the "second page" is just page 1 again,
    # which always succeeds and hides a real throttle on the cursor request.
    next_friendly = None
    next_doc_id = ""
    next_variables: dict = {}
    for name, entry in templates.items():
        if name.startswith(FRIENDLY) and "Pagination" in name and name != FRIENDLY:
            next_friendly = name
            next_doc_id = str(entry.get("client_doc_id") or "")
            next_variables = dict(entry.get("variables") or {})
            break

    client = GraphQLClient(
        token,
        proxy=args.proxy,
        timeout=args.timeout,
    )
    try:
        pages_seen = 0
        cursor_seen = False
        for payload in client.paginate(
            FRIENDLY,
            str(template.get("client_doc_id") or ""),
            variables,
            next_friendly=next_friendly,
            next_doc_id=next_doc_id,
            next_variables=next_variables,
            max_pages=max(1, args.pages),
            sleep_seconds=1.0,
        ):
            pages_seen += 1
            page_info = find_page_info(payload, REACTOR_FIELDS) or {}
            cursor_seen = bool(page_info.get("end_cursor"))
            if is_blocked(payload):
                print("BLOCKED: response carries an account-level block phrase")
                return 4
            if is_rate_limited(payload):
                codes = [
                    err.get("code")
                    for err in (payload.get("errors") or [])
                    if isinstance(err, dict) and err.get("code") in RATE_LIMIT_CODES
                ]
                print(f"THROTTLED: graphql codes {codes} on page {pages_seen}")
                return 3
    except RateLimitedError as exc:
        print(f"THROTTLED: {exc}")
        return 3
    except BlockedError as exc:
        print(f"BLOCKED: {exc}")
        return 4
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        print(f"probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(
        f"CLEAR: {pages_seen} page(s) answered, cursor={'yes' if cursor_seen else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
