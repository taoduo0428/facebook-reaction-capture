"""Owned real-job cancellation and immediate reuse acceptance; metrics only."""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8765"
TERMINAL = {"completed", "cancelled", "failed"}


def source_hashes() -> dict:
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "facebook_capture_api.py", "analyze_facebook_capture.py",
            "open_facebook_post_readonly.ps1", "explore_facebook_readonly.ps1",
            "run_facebook_capture.ps1",
        )
    }


def http(method: str, route: str, **kwargs):
    response = requests.request(method, BASE + route, timeout=(5, 90), **kwargs)
    response.raise_for_status()
    return response


def get(route: str):
    return http("GET", route).json()


def runtime_snapshot(port: int) -> dict:
    script = (
        "$ErrorActionPreference='Stop'; "
        "$p=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
        "@{n='created';e={$_.CreationDate.ToUniversalTime().ToString('o')}}); "
        f"$l=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {{$_.LocalPort -eq {int(port)}}} | "
        "Select-Object LocalPort,OwningProcess); "
        "@{processes=$p;listeners=$l} | ConvertTo-Json -Compress -Depth 4"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=25, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


def identity(process: dict) -> tuple:
    return process["ProcessId"], process["created"]


def descendants(snapshot: dict, root_pid: int) -> list[dict]:
    selected = {root_pid}
    while True:
        expanded = selected | {
            p["ProcessId"] for p in snapshot["processes"] if p["ParentProcessId"] in selected
        }
        if expanded == selected:
            break
        selected = expanded
    return [p for p in snapshot["processes"] if p["ProcessId"] in selected]


def idle(health: dict) -> bool:
    return (
        health["worker"] == "idle" and health["queued_jobs"] == 0
        and health["cancelling_jobs"] == 0
        and health["capture_lease"]["state"] == "available"
    )


def wait_idle() -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        health = get("/health")
        if idle(health):
            return health
        time.sleep(1)
    raise AssertionError("API did not release worker and lease within 90 seconds")


def complete_job(job_id: str, worker_id: str) -> dict:
    route = "/v1/jobs/" + job_id
    deadline = time.monotonic() + 600
    previous = None
    while time.monotonic() < deadline:
        job = get(route)
        if job["status"] != previous:
            print(json.dumps({"followup_job": job_id, "status": job["status"]}), flush=True)
            previous = job["status"]
        if job["status"] in TERMINAL:
            break
        time.sleep(2)
    else:
        raise AssertionError(f"Follow-up observation timed out; job retained: {job_id}")
    assert job["status"] == "completed", {k: job.get(k) for k in ("status", "error", "recovery_state")}
    assert job["worker_id"] == worker_id, "Follow-up switched workers"
    assert job.get("failover_count", 0) == 0, "Follow-up needed failover"
    assert job.get("attempts") == 1, "Follow-up needed a retry"
    result = get(route + "/result")
    reactions = result["completeness"]["reactions"]
    target = result["target_verification"]
    assert target["status"] == "matched"
    assert result["completion_scope"] == "terminal"
    assert reactions["terminal_page_seen"] is True
    assert reactions["completion_claim_allowed"] is True
    assert not reactions.get("rate_limited_records")
    assert not reactions.get("temporary_feature_block_records")
    rows = list(csv.DictReader(io.StringIO(http("GET", route + "/artifacts/facebook-reactor-users.csv?download=1").content.decode("utf-8-sig"))))
    assert len(rows) == reactions["unique_reactor_identities"]
    assert {r["source_post_id"] for r in rows} == {target["matched_post_id"]}
    assert len({r["user_id"] for r in rows}) == len(rows)
    return {
        "job_id": job_id, "worker_id": worker_id, "attempts": job["attempts"],
        "status": job["status"], "completion_scope": result["completion_scope"],
        "target_matched": True, "terminal": True, "csv_rows": len(rows),
        "seconds": round((datetime.fromisoformat(job["finished_at"]) - datetime.fromisoformat(job["started_at"])).total_seconds(), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true", help="Read-only preflight; submit nothing")
    parser.add_argument("--url", default="https://www.facebook.com/reel/1054985397317048/")
    args = parser.parse_args()
    health = get("/health")
    assert idle(health), "API not idle with an available lease"
    worker = next(w for w in health["workers"] if w["active"])
    assert worker["android"]["state"] == "device" and worker["android"]["boot_completed"]
    expected_proxy = worker["android"]["proxy"]
    assert expected_proxy and expected_proxy != f"127.0.0.1:{worker['capture_port']}"
    before = runtime_snapshot(worker["capture_port"])
    assert not before["listeners"], "Capture port was already occupied before this test"
    emulators = {identity(p) for p in before["processes"] if p["Name"].startswith("qemu")}
    print(json.dumps({"preflight": "passed", "worker_id": worker["worker_id"], "emulator_count": len(emulators)}), flush=True)
    if args.snapshot:
        return
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "accepted": False, "source_sha256": source_hashes()}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = Path(__file__).with_name(f"lifecycle-{stamp}.json")
    payload = {"post_url": args.url, "save_artifacts": ["progress", "safe_capture"]}
    try:
        submitted = http("POST", "/v1/jobs", json=payload).json()
        job_id = submitted["job_id"]
        report["cancel_job_id"] = job_id
        print(json.dumps({"cancel_job_submitted": job_id}), flush=True)
        route = "/v1/jobs/" + job_id
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            job = get(route)
            assert job["status"] not in TERMINAL, f"Job finished before trigger: {job_id}"
            if job.get("capture_run_dir"):
                path = Path(job["capture_run_dir"]).resolve()
                assert path.is_relative_to(ROOT / "runs")
                log = path / "capture.log"
                if log.is_file():
                    with log.open(encoding="utf-8-sig", errors="replace") as source:
                        if "[transport] capture proxy=" in source.read(131072):
                            break
            time.sleep(0.5)
        else:
            raise AssertionError(f"Capture stage not observed; job retained: {job_id}")
        active = runtime_snapshot(worker["capture_port"])
        owned = descendants(active, int(job["process_pid"]))
        assert owned and any(p["ProcessId"] == job["process_pid"] for p in owned)
        assert active["listeners"], "Capture port not listening at cancellation trigger"
        report["owned_processes_before"] = owned
        report["capture_listeners_before"] = active["listeners"]
        seen = [job["status"]]
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(http, "POST", route + "/cancel", json={})
            while time.monotonic() - started < 180:
                job = get(route)
                if job["status"] not in seen:
                    seen.append(job["status"])
                    print(json.dumps({"cancel_job": job_id, "status": job["status"]}), flush=True)
                if job["status"] in TERMINAL:
                    break
                time.sleep(0.25)
            future.result()
        job = get(route)
        assert job["status"] == "cancelled" and "cancelling" in seen, seen
        assert job["process_state"] == "stopped" and job.get("process_pid") is None
        assert job.get("transport_cleanup_state") == "restored", job.get("transport_cleanup_error")
        assert not job.get("transport_cleanup_error")
        after = wait_idle()
        current_worker = next(w for w in after["workers"] if w["worker_id"] == worker["worker_id"])
        assert current_worker["current_job_id"] is None
        assert current_worker["android"]["proxy"] == expected_proxy
        assert current_worker["android"]["boot_completed"] and current_worker["android"]["state"] == "device"
        stopped = runtime_snapshot(worker["capture_port"])
        live = {identity(p) for p in stopped["processes"]}
        assert not [p for p in owned if identity(p) in live], "Owned capture descendants remain"
        assert not stopped["listeners"], "Capture listener remains"
        assert emulators <= live, "An emulator was replaced during cancellation"
        report["cancellation"] = {"statuses": seen, "seconds": round(time.monotonic() - started, 2), "process_tree_stopped": True, "capture_port_free": True, "emulators_preserved": True, "proxy_restored": True, "worker_idle": True, "lease": after["capture_lease"]}
        following = http("POST", "/v1/jobs", json=payload).json()["job_id"]
        report["followup_job_id"] = following
        print(json.dumps({"followup_submitted": following}), flush=True)
        report["followup"] = complete_job(following, worker["worker_id"])
        final = wait_idle()
        assert final["active_worker_id"] == worker["worker_id"]
        assert final["android"]["proxy"] == expected_proxy
        final_runtime = runtime_snapshot(worker["capture_port"])
        assert not final_runtime["listeners"], "Capture listener remains after follow-up"
        assert emulators <= {identity(p) for p in final_runtime["processes"]}
        assert source_hashes() == report["source_sha256"], "Source changed during lifecycle acceptance"
        report["accepted"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(output), "accepted": report["accepted"]}), flush=True)


if __name__ == "__main__":
    main()
