"""Read-only HTTP acceptance inspection; print metrics, never reactor identities."""
import csv
import io
import json
from pathlib import Path
import re
import sys
from collections import Counter
from datetime import datetime

import requests


BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[2]


def get(path):
    response = requests.get(BASE + path, timeout=20)
    response.raise_for_status()
    return response


batch = get("/v1/batches/" + sys.argv[1]).json()
summary = {key: batch.get(key) for key in ("batch_id", "status", "status_counts", "completion_scope")}
posts = []
for post in batch.get("posts", []):
    job_id = post["job_id"]
    job = get("/v1/jobs/" + job_id).json()
    row = {key: post.get(key) for key in ("index", "job_id", "status", "current_stage", "valid_users", "reaction_pages", "worker_id", "serial")}
    row["attempts"] = job.get("attempts")
    row["failover_count"] = job.get("failover_count")
    row["transport_cleanup_state"] = job.get("transport_cleanup_state")
    if job.get("capture_run_dir"):
        run_path = Path(job["capture_run_dir"])
        run_path = (run_path if run_path.is_absolute() else ROOT / run_path).resolve()
        if run_path.is_relative_to(ROOT / "runs") and (run_path / "capture.log").is_file():
            log = (run_path / "capture.log").read_text(encoding="utf-8-sig", errors="replace")
            app_reuse = re.search(r"reusing existing Facebook main pid=(\d+)", log)
            hook_reuse = re.search(r"reused eternalized hooks pid=(\d+) reuse_elapsed_ms=(\d+)", log)
            row["runtime_reuse"] = {
                "facebook_pid_reused": int(app_reuse.group(1)) if app_reuse else None,
                "hooks_pid_reused": int(hook_reuse.group(1)) if hook_reuse else None,
                "hooks_reuse_ms": int(hook_reuse.group(2)) if hook_reuse else None,
                "inline_feed_promoted": "promoted verified inline feed card to post detail via timestamp" in log,
            }
    if job.get("started_at") and job.get("finished_at"):
        row["seconds"] = round((datetime.fromisoformat(job["finished_at"]) - datetime.fromisoformat(job["started_at"])).total_seconds(), 2)
    if post["status"] == "completed":
        result = get("/v1/jobs/" + job_id + "/result").json()
        row["completion_scope"] = result.get("completion_scope")
        row["target"] = result.get("target_verification", {}).get("status")
        row["target_post_id"] = result.get("target_verification", {}).get("matched_post_id")
        row["transport_observation"] = result.get("transport_observation", {})
        row["integrity"] = result.get("completeness", {}).get("reactions")
        visibility = result.get("visibility", {}).get("reactions", {})
        row["visibility"] = {key: visibility.get(key) for key in ("terminal", "reported_total", "enumerated_count", "count_difference_note")}
        response = get("/v1/jobs/" + job_id + "/artifacts/facebook-reactor-users.csv?download=1")
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        row["http_csv_rows"] = len(rows)
        row["http_csv_unique_user_ids"] = len({item.get("user_id", "") for item in rows})
        row["http_csv_sources"] = sorted({item.get("source_post_id", "") for item in rows})
    elif post["status"] == "failed":
        row["error_summary"] = str(job.get("error") or "").split("; tail=")[0]
    posts.append(row)
summary["posts"] = posts
if posts and all(post["status"] in {"completed", "failed", "cancelled"} for post in posts):
    response = get("/v1/batches/" + sys.argv[1] + "/artifacts/batch-reactor-users.csv?download=1")
    aggregate = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    summary["http_batch_csv_rows"] = len(aggregate)
    summary["http_batch_rows_by_job"] = dict(Counter(row["job_id"] for row in aggregate))
    summary["http_batch_unique_job_user_pairs"] = len({(row["job_id"], row["user_id"]) for row in aggregate})
    user_jobs = {}
    for row in aggregate:
        user_jobs.setdefault(row["user_id"], set()).add(row["job_id"])
    summary["users_preserved_across_jobs"] = sum(len(jobs) > 1 for jobs in user_jobs.values())
print(json.dumps(summary, ensure_ascii=False, indent=2))
if "--require-complete" in sys.argv[2:]:
    assert posts and all(post["status"] == "completed" for post in posts), "Batch contains incomplete jobs"
    for post in posts:
        assert post["target"] == "matched", (post["job_id"], "target mismatch")
        assert post["visibility"]["terminal"] is True, (post["job_id"], "open pagination tail")
        assert post["completion_scope"] == "terminal", post["job_id"]
        assert not post["transport_observation"].get("rate_limit_http_seen"), post["job_id"]
        assert not post["transport_observation"].get("auth_http_error_seen"), post["job_id"]
        assert post["integrity"].get("completion_claim_allowed") is True, post["job_id"]
        assert not post["integrity"].get("rate_limited_records"), post["job_id"]
        assert not post["integrity"].get("temporary_feature_block_records"), post["job_id"]
        assert len(post["http_csv_sources"]) == 1, (post["job_id"], "mixed CSV sources")
        assert post["http_csv_rows"] > 0, (post["job_id"], "known nonempty acceptance target returned no users")
        assert post["http_csv_sources"] == [post["target_post_id"]], (post["job_id"], "CSV target mismatch")
        assert post["http_csv_rows"] == post["visibility"]["enumerated_count"], post["job_id"]
        assert post["http_csv_unique_user_ids"] == post["http_csv_rows"], (post["job_id"], "duplicate CSV users")
        assert summary["http_batch_rows_by_job"][post["job_id"]] == post["http_csv_rows"], post["job_id"]
    assert summary["http_batch_csv_rows"] == sum(post["http_csv_rows"] for post in posts)
    assert summary["http_batch_unique_job_user_pairs"] == summary["http_batch_csv_rows"]
