from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


LOGIN_MARKERS = (
    "login.php",
    "log into facebook",
    "you must log in",
    "create new account",
    "login_form",
)
REACTION_MARKERS = (
    "FeedbackReactors",
    "reaction_count",
    "reactors",
    "top_reactions",
    "reaction_type",
)
PAGINATION_MARKERS = (
    "page_info",
    "end_cursor",
    "has_next_page",
    "At_Connection_Pagination",
)


class NoCookieRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        headers_out = {
            key: value
            for key, value in req.headers.items()
            if key.lower() not in {"cookie", "authorization"}
        }
        return urllib.request.Request(
            newurl,
            headers=headers_out,
            method="GET",
        )


def fetch(url: str, timeout: float = 20.0) -> dict[str, Any]:
    context = ssl.create_default_context()
    https_handler = urllib.request.HTTPSHandler(context=context)
    opener = urllib.request.build_opener(https_handler, NoCookieRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc

    raw = response.read(3_000_000)
    charset = response.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")
    lowered = text.lower()

    set_cookie_names: list[str] = []
    for value in response.headers.get_all("Set-Cookie") or []:
        name = value.split("=", 1)[0].strip()
        if name and name not in set_cookie_names:
            set_cookie_names.append(name)

    profile_ids = sorted(set(re.findall(r"profile\.php\?id=(\d{5,})", text)))
    numeric_ids = sorted(set(re.findall(r'"id"\s*:\s*"(\d{5,})"', text)))
    facebook_paths = sorted(
        set(
            re.findall(
                r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9._-]{3,80})",
                text,
                flags=re.I,
            )
        )
    )

    return {
        "requested_url": url,
        "final_url": response.geturl(),
        "status": getattr(response, "status", getattr(response, "code", None)),
        "content_type": response.headers.get("Content-Type", ""),
        "bytes_read": len(raw),
        "set_cookie_names": set_cookie_names,
        "sent_cookie": False,
        "sent_authorization": False,
        "login_wall_markers": [marker for marker in LOGIN_MARKERS if marker in lowered],
        "reaction_markers": [marker for marker in REACTION_MARKERS if marker.lower() in lowered],
        "pagination_markers": [marker for marker in PAGINATION_MARKERS if marker.lower() in lowered],
        "profile_php_ids_count": len(profile_ids),
        "json_numeric_ids_count": len(numeric_ids),
        "facebook_profile_paths_count": len(facebook_paths),
        "sample_profile_php_ids": profile_ids[:10],
        "sample_facebook_paths": facebook_paths[:10],
        "target_post_id_present": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe only Facebook content exposed to a completely anonymous HTTP client.")
    parser.add_argument("url")
    parser.add_argument("--post-id", default="")
    parser.add_argument("--out", default="anonymous-public-probe.json")
    args = parser.parse_args()

    result = fetch(args.url)
    if args.post_id:
        # Re-fetch is intentionally avoided; infer from URL/final URL and counts only.
        # The field is set to true when the post id survives in either URL. HTML is not persisted.
        result["target_post_id_present"] = args.post_id in (result.get("requested_url") or "") or args.post_id in (result.get("final_url") or "")
    out = Path(args.out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
