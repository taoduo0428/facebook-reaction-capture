"""Content baselines for finalized artifacts.

A digest is only ever taken *after* the writer finished: the caller passes
``complete=True`` for a file that was atomically published, and partial files
never get a baseline.  Hashing is streamed in fixed chunks so a multi-hundred
megabyte CSV/PCAP does not have to fit in memory.

Verification is stamp-first.  A cheap ``size:mtime_ns:inode`` stamp answers the
common case (nothing changed since publication) without re-reading the file, so
progress polling cannot turn into repeated disk I/O.  Only a changed stamp
triggers a re-hash, and a re-hash that disagrees marks the record changed rather
than silently "repairing" it.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from facebook_backend.persistence.models import ArtifactIntegrity

CHUNK = 1024 * 1024

#: Verification outcomes reported for one stored baseline.
VERIFIED = "verified"
CHANGED = "changed"
MISSING = "missing"
UNKNOWN = "unknown"


class IntegrityError(ValueError):
    """The artifact cannot be baselined (outside root, symlink, not a file)."""


@dataclass(frozen=True)
class Digest:
    """One published file's identity at publication time."""

    name: str
    sha256: str
    size: int
    stamp: str
    content_type: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "size": self.size,
            "content_type": self.content_type,
        }


def _stamp(info: os.stat_result) -> str:
    return f"{info.st_size}:{info.st_mtime_ns}:{getattr(info, 'st_ino', 0)}"


def artifact_path(root: Path, name: str) -> Path:
    """Resolve a logical artifact name inside ``root`` without escaping it."""
    from .catalog import ARTIFACT_PATHS, BATCH_ARTIFACT_SPECS

    batch = {spec[0]: spec[0] for spec in BATCH_ARTIFACT_SPECS}
    relative = ARTIFACT_PATHS.get(name) or (Path(batch[name]) if name in batch else None)
    if relative is None:
        raise IntegrityError("unknown artifact name")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise IntegrityError("artifact path escapes its run directory")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise IntegrityError("artifact path contains a symlink")
    return path


def sha256_file(path: Path) -> tuple[str, int]:
    """Stream one file; returns ``(hex digest, bytes read)``."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            total += len(block)
            digest.update(block)
    return digest.hexdigest(), total


def prepare_publication(
    root: Path,
    owner_kind: str,
    name: str,
    *,
    complete: bool = False,
    atomically_published: bool = False,
) -> Digest | None:
    """Baseline a finished artifact, or return ``None`` when it is not eligible.

    ``complete`` must be set by the writer once it knows the file is final;
    ``atomically_published`` records that the bytes appeared by atomic rename.
    Anything else yields ``None`` so a partially written file never becomes a
    baseline.
    """
    if not complete or not atomically_published:
        return None
    if owner_kind not in {"job", "batch"}:
        raise IntegrityError("owner kind must be job or batch")
    path = artifact_path(root, name)
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        return None
    digest, size = sha256_file(path)
    if size != info.st_size:
        # The file changed while it was being read: no trustworthy baseline.
        return None
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Digest(name=name, sha256=digest, size=size, stamp=_stamp(info), content_type=content_type)


def persist_publication(session: Any, owner_kind: str, owner_id: str, name: str, digest: Digest) -> None:
    """Upsert the baseline inside the caller's transaction."""
    row = session.get(ArtifactIntegrity, (owner_kind, owner_id, name))
    if row is None:
        session.add(
            ArtifactIntegrity(
                owner_kind=owner_kind,
                owner_id=owner_id,
                artifact_name=name,
                sha256=digest.sha256,
                size=digest.size,
                file_stamp=digest.stamp,
            )
        )
    else:
        row.sha256, row.size, row.file_stamp = digest.sha256, digest.size, digest.stamp
    session.flush()


def verify_artifact(root: Path, owner_kind: str, name: str, record: Mapping[str, Any]) -> str:
    """Stamp-first verification of one stored baseline."""
    try:
        path = artifact_path(root, name)
    except IntegrityError:
        return UNKNOWN
    try:
        info = path.stat()
    except OSError:
        return MISSING
    if not stat.S_ISREG(info.st_mode):
        return UNKNOWN
    if _stamp(info) == record.get("file_stamp") and int(record.get("size") or -1) == info.st_size:
        return VERIFIED
    try:
        digest, size = sha256_file(path)
    except OSError:
        return UNKNOWN
    if size == int(record.get("size") or -1) and digest == record.get("sha256"):
        return VERIFIED
    return CHANGED


def integrity_report(root: Path, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate verification for a set of baselines (job or batch)."""
    counts = {VERIFIED: 0, CHANGED: 0, MISSING: 0, UNKNOWN: 0}
    artifacts = []
    for record in records:
        outcome = verify_artifact(root, record["owner_kind"], record["artifact_name"], record)
        counts[outcome] += 1
        artifacts.append({"name": record["artifact_name"], "status": outcome, "sha256": record.get("sha256")})
    return {"artifacts": artifacts, "counts": counts, "ok": counts[VERIFIED] == len(artifacts) and bool(artifacts)}


__all__ = [
    "CHANGED",
    "Digest",
    "IntegrityError",
    "MISSING",
    "UNKNOWN",
    "VERIFIED",
    "artifact_path",
    "integrity_report",
    "persist_publication",
    "prepare_publication",
    "sha256_file",
    "verify_artifact",
]
