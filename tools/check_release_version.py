"""Check a release checkout or extracted source ZIP (stdlib only)."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = rf"{NUMBER}\.{NUMBER}\.{NUMBER}"


def check_release_version(root: Path, tag: str | None = None) -> str:
    package = (root / "facebook_backend/__init__.py").read_text(encoding="utf-8")
    versions = re.findall(rf'''^__version__\s*=\s*(["'])({VERSION})\1[ \t]*$''', package, re.MULTILINE)
    if len(versions) != 1:
        raise ValueError("facebook_backend/__init__.py: expected one literal X.Y.Z __version__")
    expected = "v" + versions[0][1]
    if tag is not None and tag != expected:
        raise ValueError(f"Git tag: expected {expected}, got {tag!r}")

    readme = (root / "README.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## 后端 ([^\s（]+)", readme, re.MULTILINE)
    if headings != [expected]:
        raise ValueError(f"README.md: expected one backend heading for {expected}, got {headings}")
    notes_path = f"docs/backend-modernization/RELEASE_NOTES_{expected}.md"
    if f"]({notes_path})" not in readme:
        raise ValueError(f"README.md: missing link to {notes_path}")

    changes = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    latest = re.search(r"^## (v\S+)(?:[ \t]|$)", changes, re.MULTILINE)
    if latest is None or latest[1] != expected:
        raise ValueError(f"CHANGELOG.md: expected first release heading to name {expected}")
    notes = (root / notes_path).read_text(encoding="utf-8")
    heading = re.match(r"# (\S+)(?:\s|$)", notes)
    if heading is None or heading[1] != expected:
        raise ValueError(f"{notes_path}: expected first heading to name {expected}")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="actual Git tag name for a tag build (omit on branch/PR builds)")
    args = parser.parse_args()
    try:
        version = check_release_version(ROOT, args.tag)
    except (OSError, ValueError) as exc:
        print(f"Release version check failed: {exc}", file=sys.stderr)
        return 1
    sources = "package, README, CHANGELOG, release notes"
    if args.tag is not None:
        sources += ", Git tag"
    print(f"{version} OK ({sources})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
