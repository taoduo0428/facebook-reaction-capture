"""One bounded acceptance batch; preserve its IDs and report, never auto-resubmit."""
import datetime as dt
import json
from pathlib import Path
import time

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "retained-optimizations"
BASE = "http://127.0.0.1:8765"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    OUT.mkdir(exist_ok=True)
    saved = OUT / "acceptance-batch.json"
    session = requests.Session()
    if saved.exists():
        identity = read_json(saved)
    else:
        health = session.get(BASE + "/health", timeout=10)
        health.raise_for_status()
        status = health.json()
        assert status["service"] == "ready" and status["worker"] == "idle" and status["queued_jobs"] == 0, status
        submitted = session.post(
            BASE + "/v1/batches",
            json={"post_urls": [
                "https://www.facebook.com/share/p/1Jetj5opcE/",
                "https://www.facebook.com/share/p/1TWnMcMtmF/",
            ], "collect": ["reactions"], "save_artifacts": ["safe_capture", "progress"]},
            timeout=30,
        )
        submitted.raise_for_status()
        identity = submitted.json()
        saved.write_text(json.dumps(identity, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"batch_id": identity["batch_id"]}), flush=True)
    batch_manifest = ROOT / "api-batches" / (identity["batch_id"] + ".batch.json")
    ids = read_json(batch_manifest)["job_ids"]
    deadline = time.monotonic() + 900
    previous = None
    while time.monotonic() < deadline:
        jobs = [read_json(ROOT / "api-jobs" / (jid + ".job.json")) for jid in ids]
        snapshot = [{"job_id": j["job_id"], "status": j["status"], "users": (j.get("checkpoint") or {}).get("reaction_users"), "error": j.get("error")} for j in jobs]
        signature = json.dumps(snapshot)
        if signature != previous:
            print(signature, flush=True)
            previous = signature
        if all(j["status"] in {"completed", "failed", "cancelled"} for j in jobs):
            break
        time.sleep(2)
    else:
        raise TimeoutError("Acceptance deadline reached; batch was not cancelled or resubmitted. IDs remain saved.")
    report = []
    for job in jobs:
        start = dt.datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
        finish = dt.datetime.fromisoformat(job["finished_at"].replace("Z", "+00:00"))
        item = {"job_id": job["job_id"], "post_url": job["request"]["post_url"], "status": job["status"], "seconds": round((finish-start).total_seconds(), 3), "completion_scope": job.get("completion_scope"), "checkpoint": job.get("checkpoint"), "error": job.get("error"), "capture_run_dir": job.get("capture_run_dir")}
        if job["status"] == "completed":
            response = session.get(BASE + "/v1/jobs/" + job["job_id"] + "/result", timeout=30)
            response.raise_for_status()
            (OUT / (job["job_id"] + "-result.json")).write_text(json.dumps(response.json(), indent=2, ensure_ascii=False), encoding="utf-8")
        report.append(item)
    (OUT / "acceptance-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    assert all(j["status"] == "completed" for j in jobs), "A batch job did not complete; inspect preserved evidence."


if __name__ == "__main__":
    main()
