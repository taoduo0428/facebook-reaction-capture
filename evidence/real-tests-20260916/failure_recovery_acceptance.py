"""Reject a deliberately mismatched real target, then complete a same-worker job."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from lifecycle_acceptance import complete_job, get, http, identity, idle, runtime_snapshot, source_hashes, wait_idle


def main():
    before = get("/health")
    assert idle(before)
    worker = next(w for w in before["workers"] if w["active"])
    assert worker["android"]["state"] == "device" and worker["android"]["boot_completed"]
    expected_proxy = worker["android"]["proxy"]
    snapshot = runtime_snapshot(worker["capture_port"])
    assert not snapshot["listeners"]
    emulators = {identity(p) for p in snapshot["processes"] if p["Name"].startswith("qemu")}
    report = {"accepted": False, "started_at": datetime.now(timezone.utc).isoformat(), "source_sha256": source_hashes()}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = Path(__file__).with_name(f"failure-recovery-{stamp}.json")
    try:
        # Both identifiers come from previously observed public targets; they are
        # intentionally inconsistent. A strict source guard must not accept it.
        payload = {
            "post_url": "https://www.facebook.com/100058288982749/posts/1476320540987565/",
            "post_id": "122122050735339946",
            "save_artifacts": ["progress", "safe_capture"],
        }
        job_id = http("POST", "/v1/jobs", json=payload).json()["job_id"]
        report["failure_job_id"] = job_id
        print(json.dumps({"expected_failure_submitted": job_id}), flush=True)
        deadline = time.monotonic() + 600
        previous = None
        while time.monotonic() < deadline:
            job = get("/v1/jobs/" + job_id)
            if job["status"] != previous:
                print(json.dumps({"job_id": job_id, "status": job["status"]}), flush=True)
                previous = job["status"]
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(2)
        else:
            raise AssertionError(f"Observation timed out; owned task retained: {job_id}")
        assert job["status"] == "failed", "Explicitly mismatched target was not rejected"
        error = str(job.get("error") or "").lower()
        assert any(word in error for word in ("target", "source", "post id", "post_id")), "Failure did not establish strict-target rejection"
        assert not any(word in error for word in ("1675004", "rate-limit", "session-restricted")), "Platform restriction prevents this acceptance"
        assert job["process_state"] == "stopped" and job.get("process_pid") is None
        after = wait_idle()
        assert after["active_worker_id"] == worker["worker_id"]
        assert after["android"]["proxy"] == expected_proxy
        assert not runtime_snapshot(worker["capture_port"])["listeners"]
        report["failure"] = {"status": "failed", "strict_target_rejected": True, "error_summary": str(job.get("error") or "").split("; tail=")[0], "process_stopped": True, "proxy_restored": True, "lease_available": True, "attempts": job.get("attempts"), "failover_count": job.get("failover_count")}
        following = http("POST", "/v1/jobs", json={"post_url": "https://www.facebook.com/reel/1054985397317048/", "save_artifacts": ["progress", "safe_capture"]}).json()["job_id"]
        report["followup_job_id"] = following
        report["followup"] = complete_job(following, worker["worker_id"])
        final = wait_idle()
        assert final["android"]["proxy"] == expected_proxy
        final_runtime = runtime_snapshot(worker["capture_port"])
        assert not final_runtime["listeners"]
        assert emulators <= {identity(p) for p in final_runtime["processes"]}
        assert source_hashes() == report["source_sha256"], "Source changed during failure recovery acceptance"
        report["accepted"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(output), "accepted": report["accepted"]}), flush=True)


if __name__ == "__main__":
    main()
