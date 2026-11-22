from pathlib import Path
base=Path(r"C:\Users\lenovo\Desktop\facebook")
api=base/"facebook_capture_api.py"
s=api.read_text(encoding="utf-8")
marker="\n\ndef _merge_reaction_progress(run_dirs: list[Path]) -> dict[str, Any]:\n"
insert='''

def _share_progress(run_dir: Path) -> dict[str, Any]:
    """Read live Share counters without treating them as accepted results."""
    live = _read_json(run_dir / "facebook-live-progress.json")
    sources = (((live.get("families") or {}).get("share") or {}).get("sources") or {})
    candidates = [value for value in sources.values() if isinstance(value, dict)] if isinstance(sources, dict) else []
    source = max(candidates, key=lambda item: int(item.get("user_count") or 0), default={})
    return {
        "share_users": int(source.get("user_count") or 0),
        "share_pages": int(source.get("page_count") or 0),
        "share_query_records": int(source.get("query_records") or 0),
        "share_terminal": bool(source.get("terminal_page_seen")),
        "share_open_page_seen": bool(source.get("open_page_seen")),
        "share_source_post_id": str(source.get("source_post_id") or ""),
        "share_updated_at": live.get("updated_at"),
    }


def _merge_share_progress(run_dirs: list[Path]) -> dict[str, Any]:
    reports = [_share_progress(run_dir) for run_dir in run_dirs]
    if not reports:
        return {}
    current = reports[0]
    return {
        "share_users": max(int(report.get("share_users") or 0) for report in reports),
        "share_pages": max(int(report.get("share_pages") or 0) for report in reports),
        "share_query_records": max(int(report.get("share_query_records") or 0) for report in reports),
        "share_terminal": bool(current.get("share_terminal")),
        "share_open_page_seen": bool(current.get("share_open_page_seen")),
        "share_source_post_id": str(current.get("share_source_post_id") or ""),
        "share_updated_at": current.get("share_updated_at"),
    }
'''
if "def _share_progress(" not in s:
    if marker not in s: raise SystemExit("merge marker missing")
    s=s.replace(marker,insert+marker,1)
old='''    metrics = _merge_reaction_progress(progress_dirs) if evidence_dirs else _reaction_progress(run_dir)
    valid_reactor_users = _count_reactor_users(progress_dirs) if evidence_dirs else _valid_reactor_user_count(run_dir)
'''
new='''    metrics = _merge_reaction_progress(progress_dirs) if evidence_dirs else _reaction_progress(run_dir)
    metrics.update(_merge_share_progress(progress_dirs) if evidence_dirs else _share_progress(run_dir))
    valid_reactor_users = _count_reactor_users(progress_dirs) if evidence_dirs else _valid_reactor_user_count(run_dir)
'''
if old not in s: raise SystemExit("build snapshot metrics marker missing")
s=s.replace(old,new,1)
old2='''            metrics = _merge_reaction_progress(evidence_dirs) if evidence_dirs else {}
            valid_users = _count_reactor_users(evidence_dirs) if evidence_dirs else 0
            result_outcome = _job_result_outcome(job, evidence_dirs, valid_users)
'''
new2='''            metrics = _merge_reaction_progress(evidence_dirs) if evidence_dirs else {}
            metrics.update(_merge_share_progress(evidence_dirs) if evidence_dirs else {})
            collect = set((job.get("request") or {}).get("collect") or ["reactions"])
            valid_users = (
                int(metrics.get("share_users") or 0)
                if collect == {"shares"}
                else _count_reactor_users(evidence_dirs) if evidence_dirs else 0
            )
            result_outcome = _job_result_outcome(job, evidence_dirs, valid_users)
'''
if old2 not in s: raise SystemExit("batch metrics marker missing")
s=s.replace(old2,new2,1)
api.write_text(s,encoding="utf-8")

app=base/"web_v2"/"app.js"
j=app.read_text(encoding="utf-8")
old3='''    item.share_summary = item.collection === "shares" && (item.status || item.job_status) === "completed"
      ? await readShareSummary(jobId) : null;
    item.result_outcome = item.share_summary?.outcome || null;
'''
new3='''    item.share_summary = item.collection === "shares" && (item.status || item.job_status) === "completed"
      ? await readShareSummary(jobId) : null;
    const metrics = item.metrics || {};
    item.share_live_summary = item.collection === "shares" && !item.share_summary &&
      (metrics.share_query_records > 0 || metrics.share_users > 0)
      ? {
          users: Number(metrics.share_users || 0),
          pages: Number(metrics.share_pages || 0),
          terminal: Boolean(metrics.share_terminal),
          detail: `实时已发现 ${Number(metrics.share_users || 0)} 个可见分享用户、${Number(metrics.share_pages || 0)} 页；完成校验前仅作进度显示。`
        }
      : null;
    item.result_outcome = item.share_summary?.outcome || null;
'''
if old3 not in j: raise SystemExit("frontend enrich marker missing")
j=j.replace(old3,new3,1)
old4='''  const summary = progress.share_summary;
'''
new4='''  const summary = progress.share_summary;
  const displayShare = summary || progress.share_live_summary;
'''
if old4 not in j: raise SystemExit("frontend summary marker missing")
j=j.replace(old4,new4,1)
old5='''  $("telUsers").textContent = shares || unknown ? String(summary?.users ?? "—") : String(m.valid_reactor_users || m.reaction_users || 0);
  $("telPages").textContent = shares || unknown ? String(summary?.pages ?? "—") : String(m.reaction_pages || 0);'''
new5='''  $("telUsers").textContent = shares || unknown ? String(displayShare?.users ?? "—") : String(m.valid_reactor_users || m.reaction_users || 0);
  $("telPages").textContent = shares || unknown ? String(displayShare?.pages ?? "—") : String(m.reaction_pages || 0);'''
if old5 not in j: raise SystemExit("frontend telemetry counters marker missing")
j=j.replace(old5,new5,1)
old6='''    termEl.textContent = summary ? "已到达（仅可见名单）" : "未验证";
    termEl.className = "tel-val tel-amber";
  }
  $("collectionResultNote").textContent = shares ? summary?.detail || "分享采集尚未取得完整性验证结果；失败或部分结果不计为成功。"'''
new6='''    termEl.textContent = summary ? "已到达（仅可见名单）" : displayShare?.terminal ? "已到达，等待校验" : displayShare ? "采集中" : "未验证";
    termEl.className = `tel-val ${summary ? "tel-emerald" : "tel-amber"}`;
  }
  $("collectionResultNote").textContent = shares ? displayShare?.detail || "分享采集尚未取得完整性验证结果；失败或部分结果不计为成功。"'''
if old6 not in j: raise SystemExit("frontend telemetry status marker missing")
j=j.replace(old6,new6,1)
app.write_text(j,encoding="utf-8")
