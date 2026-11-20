"""Bounded live checks of adaptive target navigation through the existing API."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"


def get(path):
    response = requests.get(BASE + path, timeout=(5, 20))
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--indices", default="1")
    args = parser.parse_args()
    requested = json.loads((ROOT / "evidence/real-tests-20260916/final-multi-url-request.json").read_text(encoding="utf-8"))
    indices = [int(value) for value in args.indices.split(",")]
    assert all(1 <= index <= len(requested["post_urls"]) for index in indices)
    receipt = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            "open_facebook_post_readonly.ps1", "facebook_target_readiness.py", "run_facebook_capture.ps1"
        )},
        "jobs": [],
        "accepted": False,
    }
    destination = OUT / ("navigation-wait-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")

    def save():
        destination.write_text(json.dumps(receipt, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    for index in indices:
        health = get("/health")
        assert health["worker"] == "idle" and health["capture_lease"]["state"] == "available"
        assert health["queued_jobs"] == health["cancelling_jobs"] == 0
        request = {"post_url": requested["post_urls"][index - 1], "save_artifacts": ["progress", "safe_capture"]}
        response = requests.post(BASE + "/v1/jobs", json=request, timeout=(5, 30))
        response.raise_for_status()
        submitted = response.json()
        job_id = submitted["job_id"]
        entry = {"index": index, "job_id": job_id, "request": request}
        receipt["jobs"].append(entry)
        save()
        print(json.dumps({"submitted": job_id, "index": index}), flush=True)
        deadline = time.monotonic() + 600
        last_status = None
        while True:
            state = get("/v1/jobs/" + job_id)
            if state["status"] != last_status:
                print(json.dumps({"job_id": job_id, "status": state["status"]}), flush=True)
                last_status = state["status"]
            if state["status"] in {"completed", "failed", "cancelled"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Observation timed out; job {job_id} remains intact")
            time.sleep(4)
        entry["job"] = state
        save()
        assert state["status"] == "completed", (job_id, state.get("error"))
        result = get("/v1/jobs/" + job_id + "/result")
        entry["result"] = result
        run = ROOT / "runs" / ("api-" + job_id)
        diagnostics = []
        for path in sorted(run.glob("attempt-*/target-open/target-open-diagnostics.json")):
            diagnostics.append(json.loads(path.read_text(encoding="utf-8-sig")))
        entry["target_open_diagnostics"] = diagnostics
        save()
        assert diagnostics and all(item["verified"] for item in diagnostics)
        assert all(item.get("navigation_waits") for item in diagnostics)
        print(json.dumps({"job_id": job_id, "users": len(result.get("reaction_users", [])), "completion_scope": result.get("completion_scope"), "waits": [item["navigation_waits"] for item in diagnostics]}), flush=True)
        assert result.get("completion_scope") == "terminal", result.get("completion_scope")
    health = get("/health")
    receipt["final_health"] = health
    assert health["worker"] == "idle" and health["capture_lease"]["state"] == "available"
    assert health["android"]["proxy"] == "127.0.0.1:7897"
    receipt["accepted"] = True
    save()
    print(json.dumps({"accepted": True, "evidence": str(destination)}), flush=True)


if __name__ == "__main__":
    main()
