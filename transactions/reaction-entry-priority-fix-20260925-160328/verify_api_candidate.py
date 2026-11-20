from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

project_root = Path(r'C:\\Users\\lenovo\\Desktop\\facebook')
sys.path.insert(0, str(project_root))
api_path = Path(sys.argv[1]).resolve()
evidence_dir = Path(sys.argv[2]).resolve()
spec = importlib.util.spec_from_file_location("candidate_api", api_path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
request = {
    "post_url": "https://www.facebook.com/reel/1054985397317048/",
    "post_id": "",
    "collect": ["reactions"],
    "save_artifacts": ["progress", "safe_capture"],
    "reaction_scroll_limit": 1,
    "reaction_scroll_limit_explicit": True,
}
try:
    result = module.build_api_result(evidence_dir, request, evidence_dirs=[evidence_dir])
except Exception as exc:
    print(json.dumps({"status": "error", "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, sort_keys=True))
    raise SystemExit(2)
target = result.get("target_verification") or {}
print(json.dumps({
    "status": "ok",
    "completion_scope": result.get("completion_scope"),
    "users": len(result.get("reaction_users") or []),
    "matched_by": target.get("matched_by"),
    "matched_post_id": target.get("matched_post_id"),
}, ensure_ascii=False, sort_keys=True))
