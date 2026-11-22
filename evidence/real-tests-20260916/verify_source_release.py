"""Verify exact release bytes and run the Windows CI contract in a fresh extraction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.export_source import release_files  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--checks", action="store_true")
    args = parser.parse_args()
    archive = args.archive.resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = Path(__file__).with_name(f"release-verification-{stamp}.json")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive),
        "accepted": False,
        "checks": [],
    }
    try:
        entries = dict(release_files())
        with zipfile.ZipFile(archive) as bundle:
            expected = {"facebook/" + name for name in entries} | {"facebook/SHA256SUMS.txt"}
            assert set(bundle.namelist()) == expected, "Archive member set differs from release manifest"
            assert len(bundle.namelist()) == len(expected), "Archive contains duplicate members"
            assert bundle.testzip() is None, "ZIP integrity failure"
            checksums = {}
            for line in bundle.read("facebook/SHA256SUMS.txt").decode("utf-8").splitlines():
                digest, name = line.split("  ", 1)
                assert name not in checksums
                checksums[name] = digest
            assert set(checksums) == set(entries)
            for name, expected_bytes in entries.items():
                data = bundle.read("facebook/" + name)
                assert data == expected_bytes, f"Archive source differs from workspace: {name}"
                assert hashlib.sha256(data).hexdigest() == checksums[name], name
                if name.endswith((".sh", ".command")):
                    assert b"\r\n" not in data, name
                    assert (bundle.getinfo("facebook/" + name).external_attr >> 16) & 0o111, name
            extraction = Path(tempfile.mkdtemp(prefix="facebook-release-verify-"))
            bundle.extractall(extraction)
        source = extraction / "facebook"
        report.update({
            "files": len(entries),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "exact_members": True,
            "exact_source_bytes": True,
            "manifest_hashes_valid": True,
            "extraction": str(source),
        })
        print(json.dumps({"archive_structure": "passed", "files": len(entries), "extraction": str(source)}), flush=True)
        if args.checks:
            workflow = (source / ".github/workflows/check.yml").read_text(encoding="utf-8")
            modules = []
            for line in workflow.splitlines():
                match = re.search(r"run: \.venv/Scripts/python\.exe -m unittest (.+)$", line)
                if match:
                    for module in match.group(1).split():
                        if module not in modules:
                            modules.append(module)
            assert modules, "No Windows CI unittest commands found"
            node = shutil.which("node")
            powershell = shutil.which("powershell.exe")
            assert node and powershell, "Node and Windows PowerShell are required"
            ps_checks = (
                "$ErrorActionPreference='Stop'; $files=@(Get-Content -LiteralPath 'release-files.txt' | "
                "Where-Object { $_ -like '*.ps1' }); foreach($file in $files) { $t=$null; $e=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) $file),"
                "[ref]$t,[ref]$e) | Out-Null; if($e.Count) { throw ($file + ': ' + ($e | Out-String)) } }; "
                "Write-Output ('PowerShell parsers passed: ' + $files.Count)"
            )
            commands = [
                ("ci-unittest", [sys.executable, "-m", "unittest", *modules]),
                ("node-web", [node, "--check", "web/app.js"]),
                ("node-web-v2", [node, "--check", "web_v2/app.js"]),
                ("powershell-parsers", [powershell, "-NoProfile", "-NonInteractive", "-Command", ps_checks]),
                ("pip-check", [sys.executable, "-m", "pip", "check"]),
            ]
            for label, command in commands:
                started = time.monotonic()
                completed = subprocess.run(command, cwd=source, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=600)
                log = output.with_name(f"{output.stem}-{label}.log")
                log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
                check = {
                    "name": label, "exit_code": completed.returncode,
                    "seconds": round(time.monotonic() - started, 2), "log": str(log),
                }
                count = re.search(r"Ran (\d+) tests? in", completed.stderr)
                if count:
                    check["tests_run"] = int(count.group(1))
                report["checks"].append(check)
                print(json.dumps(check), flush=True)
            assert all(check["exit_code"] == 0 for check in report["checks"]), "One or more release checks failed"
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
