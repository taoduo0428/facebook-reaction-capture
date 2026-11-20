"""Check delivery Markdown links, fences and shipped shell syntax."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ["README.md", "WINDOWS_DEPLOYMENT.md", "API_USAGE.md", "macos/QUICKSTART.md", "macos/CODEX_DEPLOYMENT_RUNBOOK.md", "macos/README.md", "VALIDATION_2026-09-16.md"]


def main():
    links = 0
    errors = []
    for name in DOCUMENTS:
        path = ROOT / name
        content = path.read_text(encoding="utf-8-sig")
        if len(re.findall(r"^```", content, re.M)) % 2:
            errors.append({"file": name, "error": "unbalanced code fence"})
        without_code = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", content, flags=re.M | re.S)
        for target in re.findall(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)", without_code):
            target = target.strip().strip("<>")
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            links += 1
            if not (path.parent / target).is_file():
                errors.append({"file": name, "target": target, "error": "missing local link"})
    git = shutil.which("git.exe")
    git_bash = Path(git).parent.parent / "bin/bash.exe" if git else None
    bash = str(git_bash) if git_bash and git_bash.is_file() else shutil.which("bash")
    assert bash, "bash is required for delivery shell syntax verification"
    shell_files = [line.strip() for line in (ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines() if line.strip().endswith((".sh", ".command"))]
    for name in shell_files:
        result = subprocess.run([bash, "-n", name], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode:
            errors.append({"file": name, "error": result.stderr[:1000]})
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "documents": DOCUMENTS, "local_links": links, "shell_files": shell_files, "errors": errors, "accepted": not errors}
    path = Path(__file__).with_name("delivery-docs-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(path)}))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
