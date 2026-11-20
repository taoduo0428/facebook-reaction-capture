from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_ENV_NAMES = ("FB_GRAPH_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN", "META_ACCESS_TOKEN")
OBJECT_ID = "61563119350848_122106880826437311"


def find_token() -> tuple[str | None, str | None]:
    for name in TOKEN_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return name, value
    return None, None


def request_json(url: str, token: str) -> tuple[int | None, dict]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "facebook-graph-capability-probe/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(256_000).decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read(256_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return None, {"transport_error": f"{type(exc).__name__}: {exc}"}
    try:
        value = json.loads(raw)
        return status, value if isinstance(value, dict) else {"raw_type": type(value).__name__}
    except json.JSONDecodeError:
        return status, {"non_json_response": True, "response_bytes": len(raw.encode("utf-8", errors="replace"))}


def summarize(status: int | None, payload: dict, token_env: str) -> dict:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), list) else []
    paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
    cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}

    identities = 0
    types = 0
    for row in data:
        if not isinstance(row, dict):
            continue
        if row.get("id") or row.get("name"):
            identities += 1
        if row.get("type"):
            types += 1

    return {
        "token_env": token_env,
        "token_value_logged": False,
        "object_id": OBJECT_ID,
        "http_status": status,
        "passed_http": status == 200,
        "reaction_rows": len(data),
        "reaction_rows_with_identity": identities,
        "reaction_rows_with_type": types,
        "paging_present": bool(paging),
        "after_cursor_present": bool(cursors.get("after") or paging.get("next")),
        "error_type": error.get("type"),
        "error_code": error.get("code"),
        "error_subcode": error.get("error_subcode"),
        "error_message": error.get("message"),
    }


def main() -> int:
    token_env, token = find_token()
    if not token:
        print(json.dumps({
            "token_available": False,
            "checked_env_names": list(TOKEN_ENV_NAMES),
            "token_value_logged": False,
            "action": "Set one supported environment variable with a legitimate Meta access token to run the capability probe.",
        }, ensure_ascii=False))
        return 3

    query = urllib.parse.urlencode({"fields": "id,name,type", "limit": "25"})
    url = f"https://graph.facebook.com/{OBJECT_ID}/reactions?{query}"
    status, payload = request_json(url, token)
    print(json.dumps({"token_available": True, **summarize(status, payload, token_env or "")}, ensure_ascii=False))
    return 0 if status == 200 else 2


if __name__ == "__main__":
    raise SystemExit(main())
