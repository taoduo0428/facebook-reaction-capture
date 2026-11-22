"""Audit the source whitelist without printing potential secret values."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.export_source import release_files


def main():
    entries = dict(release_files())
    forbidden_parts = {".venv", ".git", ".codex", "runs", "api-jobs", "api-batches", "evidence", "backups", "dist", "node_modules", "__pycache__"}
    forbidden_suffixes = {".pem", ".key", ".p12", ".pfx", ".apk", ".db", ".sqlite", ".sqlite3", ".log", ".jsonl", ".qcow2"}
    path_findings = [name for name in entries if forbidden_parts.intersection(Path(name).parts) or Path(name).suffix.lower() in forbidden_suffixes or name.endswith(".local.json")]
    ignored = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=ROOT, input=("\0".join(entries) + "\0").encode("utf-8"), capture_output=True, check=False)
    assert ignored.returncode in (0, 1), "git check-ignore failed"
    patterns = {
        "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b",
        "api-token": r"\bsk-[A-Za-z0-9_-]{24,}\b",
        "private-key": r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    }
    findings = []
    fixtures = []
    for name, data in entries.items():
        try:
            lines = data.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            for category, pattern in patterns.items():
                if not re.search(pattern, line):
                    continue
                item = {"file": name, "line": number, "category": category}
                if name == "test_facebook_mac_device.py" and category == "private-key" and ".write_text('-----BEGIN PRIVATE KEY-----', encoding='ascii')" in line:
                    fixtures.append(item)
                else:
                    findings.append(item)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "files": len(entries),
        "path_findings": path_findings,
        "ignored_entries": [name for name in ignored.stdout.decode("utf-8").split("\0") if name],
        "signature_findings": findings,
        "nonsecret_test_markers": fixtures,
        "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
        "scope": "Exact release whitelist, excluded paths, Git admission and selected secret signatures; not a general secret detector.",
    }
    report["accepted"] = not (path_findings or ignored.stdout.strip() or findings)
    destination = Path(__file__).with_name("source-manifest-audit-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}, ensure_ascii=True))
    print(json.dumps({"report": str(destination)}))
    if not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
