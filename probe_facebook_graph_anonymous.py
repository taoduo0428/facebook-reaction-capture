from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

OBJECT_IDS = [
    "122106880826437311",
    "61563119350848_122106880826437311",
]

for object_id in OBJECT_IDS:
    query = urllib.parse.urlencode({"fields": "id,reactions.limit(1){id,name,type}"})
    url = f"https://graph.facebook.com/{object_id}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "facebook-public-capability-probe/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read(8192).decode("utf-8", errors="replace")
    except Exception as exc:
        status = None
        body = f"{type(exc).__name__}: {exc}"

    print(json.dumps({"object_id": object_id, "status": status, "body": body}, ensure_ascii=False))
