"""Run one owned cancellation at the capture-proxy stage; retain only metrics."""
import concurrent.futures
import json
import time
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "failed", "cancelled"}


def request(method, route, **kwargs):
    response = requests.request(method, BASE + route, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    before = request("GET", "/health")
    assert before["worker"] == "idle", before["worker"]
    assert before["queued_jobs"] == before["cancelling_jobs"] == 0
    assert before["capture_lease"]["state"] == "available"
    expected_proxy = before["android"]["proxy"]
    worker = next(w for w in before["workers"] if w["active"])
    assert expected_proxy != f"127.0.0.1:{worker['capture_port']}"
    payload = {
        "post_url": "https://www.facebook.com/reel/1054985397317048/",
        "save_artifacts": ["progress", "safe_capture"],
    }
    submitted = request("POST", "/v1/jobs", json=payload)
    job_id = submitted["job_id"]
    print(json.dumps({"submitted": job_id, "worker_before": worker["worker_id"]}), flush=True)
    route = "/v1/jobs/" + job_id
    deadline = time.monotonic() + 240
    seen = []
    while time.monotonic() < deadline:
        job = request("GET", route)
        if job["status"] not in seen:
            seen.append(job["status"])
        if job["status"] in TERMINAL:
            raise AssertionError(f"Job became {job['status']} before cancellation trigger: {job_id}")
        capture_dir = job.get("capture_run_dir")
        if capture_dir:
            path = Path(capture_dir).resolve()
            assert path.is_relative_to(ROOT / "runs"), "Unexpected capture path"
            log_path = path / "capture.log"
            if log_path.is_file():
                with log_path.open("r", encoding="utf-8-sig", errors="replace") as source:
                    log = source.read(131072)
                if "[transport] capture proxy=" in log:
                    break
        time.sleep(1)
    else:
        raise AssertionError(f"Capture proxy stage not reached: {job_id}; job left intact")
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        cancellation = executor.submit(request, "POST", route + "/cancel", json={})
        while time.monotonic() - started < 180:
            job = request("GET", route)
            if job["status"] not in seen:
                seen.append(job["status"])
                print(json.dumps({"job_id": job_id, "status": job["status"], "process_state": job.get("process_state")}), flush=True)
            if job["status"] in TERMINAL:
                break
            time.sleep(0.5)
        cancellation.result()
    assert job["status"] == "cancelled", job["status"]
    assert "cancelling" in seen, seen
    assert job["process_state"] == "stopped"
    assert job.get("process_pid") is None
    for _ in range(30):
        after = request("GET", "/health")
        if after["worker"] == "idle" and after["capture_lease"]["state"] == "available":
            break
        time.sleep(1)
    assert after["worker"] == "idle", after["worker"]
    assert after["queued_jobs"] == after["cancelling_jobs"] == 0
    assert after["capture_lease"]["state"] == "available"
    job = request("GET", route)
    assert job.get("transport_cleanup_state") == "restored", job.get("transport_cleanup_state")
    assert not job.get("transport_cleanup_error"), job.get("transport_cleanup_error")
    cancelled_worker = next(w for w in after["workers"] if w["worker_id"] == job["worker_id"])
    assert cancelled_worker["android"]["proxy"] == expected_proxy, cancelled_worker["android"]
    result = {
        "job_id": job_id,
        "statuses": seen,
        "seconds_after_cancel": round(time.monotonic() - started, 2),
        "worker_id": job["worker_id"],
        "process_state": job["process_state"],
        "normal_proxy_restored": True,
        "capture_lease": after["capture_lease"],
        "cleanup": {key: value for key, value in job.items() if "cleanup" in key or "transport" in key},
    }
    output = Path(__file__).with_name(f"cancel-{job_id}.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
