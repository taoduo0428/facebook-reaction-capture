"""Replay Facebook GraphQL connection queries over plain HTTP.

A capture is needed only once: it yields the OAuth token, the persisted query
id (``client_doc_id``) and the variables template.  After that the same
connection can be paginated with plain HTTP -- no UI, no clicks, no scrolling.

The module never writes the token to disk and never prints it.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import requests
import zstandard
from mitmproxy import http, io

GRAPHQL_ENDPOINT = "https://graph.facebook.com/graphql"
DEFAULT_UA = "Facebook/555.0.0.49.59 (Android 15)"
REACTOR_FIELDS = ("unified_reactors", "reactors", "important_reactors", "friend_reactors")

# Facebook's own throttle codes, already recognised by the capture analyzer.
# 1675004 = reaction rate limit, 3252001 = temporary feature block, 368 = api error.
RATE_LIMIT_CODES = {1675004, 3252001, 368}
RATE_LIMIT_BACKOFF_MAX = 10.0

# Copy taken from the checkpoint / temporary-block pages that the established
# scrapers watch for (kevinzg/facebook-scraper raises AccountDisabled on these).
# Match the stable fragments, not whole sentences: Facebook varies the leading
# "You are" / "You're" wording and the apostrophe character.
BLOCK_PHRASES = (
    "temporarily blocked",
    "use this feature at the moment",
    "use this feature right now",
    "suspended your account",
    "login approval needed",
)


class RateLimitedError(RuntimeError):
    """Raised when Facebook answers with an HTTP-level throttle."""


class BlockedError(RuntimeError):
    """Raised when Facebook reports a checkpoint or temporary block."""


def is_blocked(text) -> bool:
    """True when the text (or a response payload) carries a block phrase."""
    if isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    lowered = str(text or "").lower()
    return any(phrase in lowered for phrase in BLOCK_PHRASES)


def is_rate_limited(payload: dict) -> bool:
    """True when the response carries a Facebook throttle signal."""
    if not isinstance(payload, dict):
        return False
    for error in payload.get("errors") or []:
        if not isinstance(error, dict):
            continue
        if error.get("code") in RATE_LIMIT_CODES:
            return True
        message = str(error.get("message") or "").lower()
        if "rate limit" in message or "temporarily blocked" in message:
            return True
    return False


def decode_body(raw: bytes, encoding: str = "") -> bytes:
    """Undo the gzip/zstd transfer encoding Facebook uses on this endpoint."""
    if not raw:
        return b""
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    if "zstd" in (encoding or "").lower():
        try:
            return zstandard.ZstdDecompressor().decompress(raw)
        except zstandard.ZstdError:
            pass
    return raw


def read_capture(path: Path):
    """Yield one row per GraphQL request found in a mitmproxy flow archive."""
    with path.open("rb") as handle:
        for flow in io.FlowReader(handle).stream():
            if not isinstance(flow, http.HTTPFlow):
                continue
            encoding = str(flow.request.headers.get("content-encoding") or "")
            body = decode_body(flow.request.raw_content or b"", encoding)
            try:
                form = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
            except ValueError:
                continue
            friendly = form.get("fb_api_req_friendly_name") or str(
                flow.request.headers.get("x-fb-friendly-name") or ""
            )
            doc_id = form.get("client_doc_id") or form.get("query_id") or ""
            if not friendly or not doc_id:
                continue
            try:
                variables = json.loads(form.get("variables") or "{}")
            except json.JSONDecodeError:
                variables = {}
            yield {
                "friendly_name": friendly,
                "client_doc_id": doc_id,
                "variables": variables,
                "authorization": str(flow.request.headers.get("authorization") or ""),
                "status": flow.response.status_code if flow.response else None,
            }


def collect_templates(path: Path):
    """Return (friendly_name -> template, oauth token) from one capture."""
    templates: dict[str, dict] = {}
    token = ""
    for row in read_capture(path):
        templates.setdefault(
            row["friendly_name"],
            {"client_doc_id": row["client_doc_id"], "variables": row["variables"]},
        )
        if not token and row["authorization"].lower().startswith("oauth "):
            token = row["authorization"].split(" ", 1)[1].strip()
    return templates, token


def normalize_post_id(raw) -> str | None:
    """Turn one feed post_id cell into a bare numeric id.

    Feed rows mix plain ids ("1691321539667513") with composites
    ("642731516961998_1797199558181849"); the post id is the last segment.
    """
    if raw is None:
        return None
    candidate = str(raw).strip().split("_")[-1].strip()
    if not candidate.isdigit():
        return None
    if not 6 <= len(candidate) <= 32:
        return None
    return candidate


def discover_post_ids(
    posts_csv: Path,
    exclude: str = "",
    limit: int = 10,
) -> list[str]:
    """Collect post ids from an analyzed feed CSV, minus the focus post."""
    found: list[str] = []
    seen: set[str] = set()
    try:
        with posts_csv.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                candidate = normalize_post_id(row.get("post_id"))
                if not candidate:
                    continue
                if candidate == str(exclude or "").strip():
                    continue
                if candidate in seen:
                    continue
                seen.add(candidate)
                found.append(candidate)
                if limit and limit > 0 and len(found) >= limit:
                    break
    except OSError:
        return []
    return found


class GraphQLClient:
    """Minimal persisted-query client for graph.facebook.com/graphql."""

    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        gzip_body: bool = True,
        timeout: int = 20,
    ):
        if not token:
            raise ValueError("an OAuth token is required")
        self.token = token
        self.gzip_body = gzip_body
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "authorization": f"OAuth {token}",
                "user-agent": DEFAULT_UA,
                "accept-encoding": "zstd",
            }
        )
        # graph.facebook.com is unreachable from this host without the project
        # upstream proxy, so honour it instead of failing with ConnectTimeout.
        proxy = proxy or os.environ.get("FACEBOOK_UPSTREAM_PROXY") or ""
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def call(self, friendly: str, doc_id: str, variables: dict) -> dict:
        payload = urlencode(
            {
                "client_doc_id": doc_id,
                "fb_api_req_friendly_name": friendly,
                "fb_api_caller_class": "graphservice",
                "fb_api_client_context": '{"is_background":false}',
                "variables": json.dumps(variables, separators=(",", ":")),
                "method": "post",
                "format": "json",
                "locale": "zh_CN",
                "pretty": "false",
                "purpose": "fetch",
                "server_timestamps": "true",
            }
        ).encode()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": friendly,
        }
        if self.gzip_body:
            payload = gzip.compress(payload)
            headers["content-encoding"] = "gzip"
        response = self.session.post(
            GRAPHQL_ENDPOINT, data=payload, headers=headers, timeout=self.timeout
        )
        raw = decode_body(
            response.content, str(response.headers.get("content-encoding") or "")
        )
        if response.status_code == 429:
            raise RateLimitedError("HTTP 429 from graph.facebook.com/graphql")
        if response.status_code != 200:
            text = raw.decode("utf-8", "replace")
            if is_blocked(text):
                raise BlockedError(
                    f"HTTP {response.status_code}: Facebook checkpoint or temporary block"
                )
            raise RuntimeError(f"HTTP {response.status_code}: {raw[:200]!r}")
        text = raw.decode("utf-8", "replace")
        # A 200 can still be a checkpoint page on the mobile endpoint.
        if is_blocked(text):
            raise BlockedError("Facebook returned a checkpoint or temporary block page")
        return json.loads(raw)

    def paginate(
        self,
        friendly: str,
        doc_id: str,
        variables: dict,
        *,
        next_friendly: str | None = None,
        next_doc_id: str | None = None,
        next_variables: dict | None = None,
        first_field: str | None = None,
        cursor_field: str | None = None,
        page_info_fields: tuple[str, ...] = (),
        first: int = 25,
        max_pages: int = 200,
        sleep_seconds: float = 1.0,
        state_path: Path | None = None,
    ):
        """Yield one payload per page.

        Facebook splits connections in two: the first page uses the plain
        query, and every later page switches to a dedicated
        ``..._At_Connection_Pagination_...`` query whose variables carry
        ``paginationPK``/``orderby`` plus the ``*_after_cursor`` value.  Field
        names are detected from the captured template so no family-specific
        hardcoding is required.

        ``state_path`` freezes progress after every page (cursor + page count)
        so an interrupted batch can resume instead of re-fetching from page 1.
        """
        if first_field is None:
            first_field = next(
                (key for key in variables if key.endswith("_first")), "first"
            )
        if cursor_field is None:
            # The cursor key only exists in the pagination template, not in the
            # first-page variables, so detect it from whichever template has it.
            cursor_source = next_variables if next_variables is not None else variables
            cursor_field = next(
                (key for key in cursor_source if key.endswith("_after_cursor")), "after"
            )

        cursor = None
        pages_done = 0
        if state_path is not None:
            state_file = Path(state_path)
            if state_file.is_file():
                try:
                    saved = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    saved = {}
                if saved.get("friendly") == friendly and saved.get("cursor"):
                    cursor = str(saved["cursor"])
                    pages_done = int(saved.get("pages") or 0)
                    print(
                        f"resuming {friendly} at page {pages_done + 1}",
                        file=sys.stderr,
                    )

        current_sleep = max(0.0, sleep_seconds)
        for page in range(max(1, max_pages)):
            # A resumed run already holds a cursor, so it must use the
            # pagination template even on its first iteration.
            if (page == 0 and not cursor) or not next_friendly:
                call_friendly, call_doc_id = friendly, doc_id
                page_variables = dict(variables)
            else:
                call_friendly = next_friendly
                call_doc_id = next_doc_id or doc_id
                page_variables = dict(
                    next_variables if next_variables is not None else variables
                )
            page_variables[first_field] = first
            if cursor:
                page_variables[cursor_field] = cursor
            # An HTTP-level throttle retries the same page after backing off;
            # a checkpoint aborts instead of burning the account.
            for attempt in range(3):
                try:
                    payload = self.call(call_friendly, call_doc_id, page_variables)
                    break
                except RateLimitedError:
                    current_sleep = min(
                        max(current_sleep * 2, 1.0), RATE_LIMIT_BACKOFF_MAX
                    )
                    print(
                        f"HTTP 429; backing off {current_sleep:.1f}s "
                        f"(retry {attempt + 1}/3)",
                        file=sys.stderr,
                    )
                    time.sleep(current_sleep)
            else:
                raise RateLimitedError("graph.facebook.com kept returning HTTP 429")
            yield payload

            # Facebook publishes no per-endpoint budget, so react to its own
            # throttle codes instead of guessing a fixed delay: back off hard
            # when throttled, then drift back toward the requested interval.
            if is_rate_limited(payload):
                current_sleep = min(
                    max(current_sleep * 2, 1.0), RATE_LIMIT_BACKOFF_MAX
                )
                print(
                    f"rate limited by Facebook; backing off to {current_sleep:.1f}s",
                    file=sys.stderr,
                )
            elif current_sleep > sleep_seconds:
                current_sleep = max(sleep_seconds, current_sleep * 0.9)

            page_info = find_page_info(payload, page_info_fields)
            if is_rate_limited(payload) and (not page_info or not page_info.get("has_next_page")):
                # A throttled response carries no usable page_info, so ending the
                # loop here would look like a legitimately short final page and
                # silently truncate the roster.  Fail loudly instead.
                raise RateLimitedError(
                    f"rate limited at page {pages_done + page + 1}; roster incomplete"
                )
            if not page_info or not page_info.get("has_next_page"):
                if state_path is not None:
                    try:
                        Path(state_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                return
            cursor = page_info.get("end_cursor")
            if not cursor:
                return
            if state_path is not None:
                try:
                    state_file = Path(state_path)
                    state_file.parent.mkdir(parents=True, exist_ok=True)
                    state_file.write_text(
                        json.dumps(
                            {
                                "friendly": friendly,
                                "cursor": cursor,
                                "pages": pages_done + page + 1,
                                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    print(f"state write failed: {exc}", file=sys.stderr)
            if current_sleep:
                time.sleep(current_sleep)


def find_page_info(node, prefer_fields=()):
    """Return the page_info of the requested connection, else the first one.

    A single Facebook response can carry several connections (the target one,
    a top-story prefetch, an invitable count...).  Picking the first page_info
    blindly advances the wrong cursor, which silently replays the same page.
    """
    if isinstance(node, dict):
        for field in prefer_fields:
            connection = node.get(field)
            if isinstance(connection, dict):
                page_info = connection.get("page_info")
                if isinstance(page_info, dict) and "has_next_page" in page_info:
                    return page_info
        page_info = node.get("page_info")
        if isinstance(page_info, dict) and "has_next_page" in page_info:
            return page_info
        for value in node.values():
            found = find_page_info(value, prefer_fields)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_page_info(value, prefer_fields)
            if found:
                return found
    return None


def iter_reactors(payload: dict):
    """Yield (user_id, user_name, reaction_id) from any reactor connection."""
    for field in REACTOR_FIELDS:
        for node in _find_connections(payload, field):
            edges = node.get("edges")
            if isinstance(edges, list):
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    actor = edge.get("node") or {}
                    user_id = actor.get("id") or actor.get("strong_id__")
                    if user_id:
                        reaction = (edge.get("feedback_reaction_info") or {}).get("id")
                        yield str(user_id), str(actor.get("name") or ""), reaction


def iter_shares(payload: dict):
    """Yield one row per resharer from a ``reshares`` connection.

    Facebook nests the sharer inside ``node.actors[]`` and keeps the share's own
    story id / permalink on the node, so a plain reactor walk misses everything.
    """
    for connection in _find_connections(payload, "reshares"):
        for edge in connection.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node") or {}
            actors = [a for a in (node.get("actors") or []) if isinstance(a, dict)]
            actor = actors[0] if actors else {}
            actor_id = actor.get("strong_id__") or actor.get("id")
            if not actor_id:
                continue
            yield {
                "actor_id": str(actor_id),
                "actor_name": str(actor.get("name") or ""),
                "share_url": str(node.get("url") or ""),
                "share_story_id": str(node.get("id") or ""),
                "share_post_id": str(node.get("post_id") or ""),
            }


def _find_connections(node, field):
    if isinstance(node, dict):
        connection = node.get(field)
        if isinstance(connection, dict) and (
            "edges" in connection or "nodes" in connection or "page_info" in connection
        ):
            yield connection
        for value in node.values():
            yield from _find_connections(value, field)
    elif isinstance(node, list):
        for value in node:
            yield from _find_connections(value, field)


def list_templates(templates: dict, *, show_variables: bool) -> int:
    if not templates:
        print("no GraphQL templates with client_doc_id found", file=sys.stderr)
        return 1
    for friendly in sorted(templates):
        template = templates[friendly]
        print(f"{friendly}\tdoc_id={template['client_doc_id']}")
        if show_variables:
            print(f"    variables={json.dumps(template['variables'], ensure_ascii=False)[:300]}")
    print(f"\ntotal={len(templates)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay Facebook GraphQL connections from a captured mitmproxy archive."
    )
    parser.add_argument("--mitm", type=Path, help="raw-facebook-http.mitm")
    parser.add_argument(
        "--template-json",
        type=Path,
        help="persisted {'friendly': {'client_doc_id', 'variables'}} templates; "
        "lets a direct fetch run from a target-open capture that never carried "
        "the reaction/share queries",
    )
    parser.add_argument(
        "--discover-post-ids",
        type=Path,
        help="analyzed feed CSV; print the post ids it exposes and exit",
    )
    parser.add_argument(
        "--discover-exclude",
        default="",
        help="post id to leave out (the run's focus post)",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=10,
        help="stop after this many ids (0 = no limit)",
    )
    parser.add_argument(
        "--dump-templates",
        type=Path,
        help="write the captured templates to this JSON file and exit",
    )
    parser.add_argument("--list", action="store_true", help="list friendly_name -> client_doc_id")
    parser.add_argument("--show-variables", action="store_true", help="include the captured variables")
    parser.add_argument("--friendly", help="friendly_name to replay")
    parser.add_argument("--doc-id", help="override client_doc_id")
    parser.add_argument("--variables", help="JSON variables; defaults to the captured template")
    parser.add_argument("--first", type=int, default=25)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="seconds between pages (default 1.0s). Lowering it raises rate-limit "
        "and account-restriction risk; only do that deliberately.",
    )
    parser.add_argument(
        "--post-ids",
        help="comma-separated numeric post ids; fetch every post concurrently "
        "(one feedback_id per post, same doc_id/token)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="concurrent posts when --post-ids is used (default 2; higher values "
        "increase the chance of Facebook rate limiting)",
    )
    parser.add_argument(
        "--no-pagination-template",
        action="store_true",
        help="do not switch to the sibling ..._Pagination_... query after page 1",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="freeze cursor/page progress to this file and resume from it next run",
    )
    parser.add_argument(
        "--proxy",
        help="upstream proxy for graph.facebook.com (defaults to FACEBOOK_UPSTREAM_PROXY)",
    )
    parser.add_argument(
        "--jsonl-out",
        type=Path,
        help="append every page as a capture-compatible JSONL record so the "
        "existing analyzer can consume a direct fetch",
    )
    parser.add_argument(
        "--source-post-id",
        help="numeric post id stamped on every JSONL record (required with --jsonl-out)",
    )
    parser.add_argument("--out", type=Path, help="write reactor rows to this CSV")
    args = parser.parse_args(argv)

    if args.discover_post_ids:
        # Pure CSV work: no capture, no network.
        ids = discover_post_ids(
            args.discover_post_ids,
            exclude=args.discover_exclude,
            limit=args.discover_limit,
        )
        if not ids:
            print("no post ids found in the feed csv", file=sys.stderr)
            return 1
        print(",".join(ids))
        return 0

    if not args.mitm or not args.mitm.is_file():
        print(f"capture not found: {args.mitm}", file=sys.stderr)
        return 2

    templates, token = collect_templates(args.mitm)
    if args.template_json:
        # Persisted templates beat capture-derived ones: a target-open capture
        # has no reaction/share query to learn a doc_id from.
        try:
            persisted = json.loads(args.template_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"template json unreadable: {exc}", file=sys.stderr)
            return 2
        # The live capture wins for names it carries: its variables track the
        # current app session.  The persisted file only fills the gaps, which
        # is what lets a target-open capture (no reaction query) still page.
        merged = dict(persisted)
        merged.update(templates)
        templates = merged
    if args.dump_templates:
        args.dump_templates.parent.mkdir(parents=True, exist_ok=True)
        args.dump_templates.write_text(
            json.dumps(templates, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"wrote {len(templates)} templates to {args.dump_templates}", file=sys.stderr)
        return 0
    if args.list or (not args.friendly and not args.post_ids):
        return list_templates(templates, show_variables=args.show_variables)

    friendly = args.friendly or "FeedbackReactorsGraphService"
    template = templates.get(friendly)
    if template is None:
        print(f"friendly_name not in capture: {friendly}", file=sys.stderr)
        return 2
    if not token:
        print("capture has no OAuth authorization header", file=sys.stderr)
        return 2

    # Later pages use a sibling "..._At_Connection_Pagination_..." query, so
    # pick it up from the same capture instead of guessing variable names.
    next_friendly = None
    next_doc_id = None
    next_variables = None
    if not args.no_pagination_template:
        next_friendly = next(
            (
                name
                for name in templates
                if name.startswith(friendly)
                and "Pagination" in name
                and name != friendly
            ),
            None,
        )
        if next_friendly:
            next_doc_id = templates[next_friendly]["client_doc_id"]
            next_variables = templates[next_friendly]["variables"]

    if args.post_ids:
        post_ids = [x.strip() for x in args.post_ids.split(",") if x.strip().isdigit()]
        if not post_ids:
            print("--post-ids had no numeric post ids", file=sys.stderr)
            return 2
        doc_id = args.doc_id or template["client_doc_id"]
        jsonl_lock = threading.Lock()

        def fetch_one(post_id: str):
            client = GraphQLClient(token, proxy=args.proxy)
            feedback_id = base64.b64encode(f"feedback:{post_id}".encode()).decode()
            variables = dict(template["variables"])
            variables["feedback_id"] = feedback_id
            # Per-post resume state: when Facebook throttles mid-batch the cursor
            # survives, so the next run continues instead of replaying page 1.
            post_state = None
            if args.state:
                state_root = args.state
                if state_root.suffix:
                    state_root = state_root.parent / state_root.stem
                state_root.mkdir(parents=True, exist_ok=True)
                post_state = state_root / f"{post_id}.json"
            rows = []
            pages = 0
            try:
                for payload in client.paginate(
                    friendly,
                    doc_id,
                    variables,
                    next_friendly=next_friendly,
                    next_doc_id=next_doc_id,
                    next_variables=next_variables,
                    page_info_fields=REACTOR_FIELDS if "Reactor" in friendly else ("reshares",),
                    first=args.first,
                    max_pages=args.max_pages,
                    sleep_seconds=args.sleep,
                    state_path=post_state,
                ):
                    pages += 1
                    rows.extend(
                        iter_shares(payload) if "Reshare" in friendly else iter_reactors(payload)
                    )
                    if args.jsonl_out:
                        record = {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                            "friendly_name": friendly,
                            "status_code": 200,
                            "response_body": json.dumps(payload, ensure_ascii=False),
                            "source": "direct-graphql",
                            "request_feedback_post_id": str(post_id),
                        }
                        with jsonl_lock:
                            args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
                            with args.jsonl_out.open("a", encoding="utf-8") as handle:
                                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as exc:  # one post must not abort the batch
                return post_id, rows, pages, f"{type(exc).__name__}: {exc}"[:120]
            return post_id, rows, pages, None

        started = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(fetch_one, post_ids))
        elapsed = time.time() - started
        total_rows = sum(len(rows) for _, rows, _, _ in results)
        failures = [(pid, err) for pid, _, _, err in results if err]
        for post_id, rows, pages, err in results:
            state = err or f"pages={pages} rows={len(rows)}"
            print(f"{post_id}\t{state}", file=sys.stderr)
        print(
            f"posts={len(post_ids)} rows={total_rows} failures={len(failures)} "
            f"workers={args.workers} elapsed={elapsed:.1f}s",
            file=sys.stderr,
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                if "Reshare" in friendly:
                    writer.writerow(
                        ["post_id", "actor_id", "actor_name", "share_url",
                         "share_story_id", "share_post_id"]
                    )
                    for post_id, rows, _, _ in results:
                        for row in rows:
                            writer.writerow(
                                [post_id, row["actor_id"], row["actor_name"],
                                 row["share_url"], row["share_story_id"], row["share_post_id"]]
                            )
                else:
                    writer.writerow(["post_id", "user_id", "user_name", "reaction_id"])
                    for post_id, rows, _, _ in results:
                        for row in rows:
                            writer.writerow([post_id, row[0], row[1], row[2]])
            print(f"wrote {args.out}", file=sys.stderr)
        return 0

    doc_id = args.doc_id or template["client_doc_id"]
    variables = json.loads(args.variables) if args.variables else dict(template["variables"])

    client = GraphQLClient(token, proxy=args.proxy)
    if next_friendly:
        print(f"pagination_template={next_friendly}", file=sys.stderr)

    rows = []
    pages = 0
    is_shares = "Reshare" in args.friendly
    for payload in client.paginate(
        args.friendly,
        doc_id,
        variables,
        next_friendly=next_friendly,
        next_doc_id=next_doc_id,
        next_variables=next_variables,
        page_info_fields=REACTOR_FIELDS if "Reactor" in args.friendly else ("reshares",),
        first=args.first,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep,
        state_path=args.state,
    ):
        pages += 1
        rows.extend(iter_shares(payload) if is_shares else iter_reactors(payload))
        if args.jsonl_out:
            # Emit the same shape the mitmproxy addon writes, so the existing
            # analyzer can consume a direct fetch without special cases.
            record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                "friendly_name": friendly,
                "status_code": 200,
                "response_body": json.dumps(payload, ensure_ascii=False),
                "source": "direct-graphql",
            }
            if args.source_post_id:
                record["request_feedback_post_id"] = str(args.source_post_id)
            args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl_out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        page_info = find_page_info(payload) or {}
        print(
            f"page={pages} rows={len(rows)} has_next={page_info.get('has_next_page')}",
            file=sys.stderr,
        )

    if is_shares:
        unique_shares = {
            (row["actor_id"], row["share_story_id"]): row for row in rows
        }
        unique = unique_shares
        print(
            f"pages={pages} rows={len(rows)} unique={len(unique_shares)}", file=sys.stderr
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["actor_id", "actor_name", "share_url", "share_story_id", "share_post_id"]
                )
                for row in sorted(unique_shares.values(), key=lambda r: r["actor_id"]):
                    writer.writerow(
                        [
                            row["actor_id"],
                            row["actor_name"],
                            row["share_url"],
                            row["share_story_id"],
                            row["share_post_id"],
                        ]
                    )
            print(f"wrote {args.out}", file=sys.stderr)
        return 0

    unique = {(row[0], row[2]) for row in rows}
    print(f"pages={pages} rows={len(rows)} unique={len(unique)}", file=sys.stderr)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["user_id", "user_name", "reaction_id"])
            writer.writerows(sorted(unique))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
