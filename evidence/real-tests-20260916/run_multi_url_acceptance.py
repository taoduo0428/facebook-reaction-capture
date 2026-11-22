"""Submit one owned sequential batch, persist its ID, and verify HTTP CSV metrics."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import requests

DIRECTORY = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"


def get(route):
    response = requests.get(BASE + route, timeout=(5, 20))
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-index", type=int, help="Run only this 1-based entry from the unchanged five-URL request")
    args = parser.parse_args()
    health = get("/health")
    assert health["worker"] == "idle" and health["capture_lease"]["state"] == "available"
    assert health["queued_jobs"] == health["cancelling_jobs"] == 0
    request = json.loads((DIRECTORY / "final-multi-url-request.json").read_text(encoding="utf-8"))
    if args.post_index is not None:
        if not 1 <= args.post_index <= len(request["post_urls"]):
            parser.error("--post-index is outside the request's URL list")
        request["post_urls"] = [request["post_urls"][args.post_index - 1]]
    source_hashes = {
        name: hashlib.sha256((DIRECTORY.parent.parent / name).read_bytes()).hexdigest()
        for name in (
            "facebook_capture_api.py", "analyze_facebook_capture.py",
            "open_facebook_post_readonly.ps1", "explore_facebook_readonly.ps1",
            "run_facebook_capture.ps1",
        )
    }
    response = requests.post(BASE + "/v1/batches", json=request, timeout=(5, 30))
    response.raise_for_status()
    submitted = response.json()
    batch_id = submitted["batch_id"]
    receipt = {"batch_id": batch_id, "submitted_at": datetime.now(timezone.utc).isoformat(), "request": request, "source_sha256": source_hashes}
    (DIRECTORY / f"final-{batch_id}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"submitted_batch": batch_id, "url_count": len(request["post_urls"])}), flush=True)
    deadline = time.monotonic() + 1800
    previous = None
    failures = 0
    restriction_stop = False
    while time.monotonic() < deadline:
        try:
            batch = get("/v1/batches/" + batch_id)
            failures = 0
        except requests.RequestException as exc:
            failures += 1
            print(json.dumps({"batch_id": batch_id, "observation_error": type(exc).__name__, "consecutive": failures}), flush=True)
            if failures >= 3:
                raise RuntimeError(f"Observation unavailable; batch {batch_id} remains intact") from exc
            time.sleep(3)
            continue
        summary = {"batch_id": batch_id, "status": batch["status"], "posts": [{k: p.get(k) for k in ("index", "job_id", "status", "current_stage", "valid_users", "reaction_pages")} for p in batch["posts"]]}
        serialized = json.dumps(summary, ensure_ascii=True)
        if serialized != previous:
            print(serialized, flush=True)
            previous = serialized
        if not restriction_stop:
            for post in batch["posts"]:
                if post["status"] != "failed":
                    continue
                error = str(get("/v1/jobs/" + post["job_id"]).get("error") or "").lower()
                if any(marker in error for marker in ("1675004", "rate-limit", "rate_limit", "session-restricted", "temporary-feature", "captcha", "login-required")):
                    stopped = requests.post(BASE + "/v1/batches/" + batch_id + "/cancel", json={}, timeout=(5, 90))
                    stopped.raise_for_status()
                    restriction_stop = True
                    print(json.dumps({"batch_id": batch_id, "restriction_stop": True, "failed_job": post["job_id"]}), flush=True)
                    break
        if all(p["status"] in {"completed", "failed", "cancelled"} for p in batch["posts"]):
            break
        time.sleep(4)
    else:
        raise RuntimeError(f"Observation deadline reached; batch {batch_id} remains intact")
    inspected = subprocess.run([sys.executable, "-X", "utf8", str(DIRECTORY / "inspect_batch.py"), batch_id, "--require-complete"], capture_output=True, text=True, encoding="utf-8", timeout=180)
    output = DIRECTORY / f"final-{batch_id}-acceptance.json"
    output.write_text(inspected.stdout, encoding="utf-8")
    if inspected.returncode:
        (DIRECTORY / f"final-{batch_id}-errors.txt").write_text(inspected.stderr, encoding="utf-8")
        print(json.dumps({"batch_id": batch_id, "accepted": False, "metrics": str(output)}), flush=True)
        raise SystemExit(inspected.returncode)
    metrics = json.loads(inspected.stdout)
    assert all(
        hashlib.sha256((DIRECTORY.parent.parent / name).read_bytes()).hexdigest() == digest
        for name, digest in source_hashes.items()
    ), "Source changed during real-URL acceptance"
    print(json.dumps({"batch_id": batch_id, "accepted": True, "aggregate_csv_rows": metrics["http_batch_csv_rows"], "metrics": str(output)}), flush=True)


if __name__ == "__main__":
    main()
