"""Artifact lookup and download metadata.

Path safety stays where it already is: ``JobManager.artifact`` /
``batch_artifact`` run ``_checked_artifact_path`` (anchor/``..`` rejection,
symlink refusal, resolved-path confinement, non-empty file).  This service adds
only transport metadata, never a second, weaker path check.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from facebook_backend.compatibility.legacy import inline_artifact_suffixes, map_legacy_errors
from facebook_backend.domain.exceptions import NotFoundError
from facebook_backend.domain.ports import JobManagerPort

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    path: Path
    size: int
    content_type: str
    disposition: str
    chunk_size: int = CHUNK_SIZE


def _describe(path: Path, *, download: bool) -> ArtifactFile:
    suffix = path.suffix.lower()
    # Byte-for-byte legacy rule: CSV is the only type that gets a charset, every
    # other artifact uses the raw ``mimetypes`` answer (e.g. "application/json").
    if suffix == ".csv":
        content_type = "text/csv; charset=utf-8"
    else:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inline_ok = suffix in inline_artifact_suffixes()
    disposition = "attachment" if download or not inline_ok else "inline"
    try:
        size = path.stat().st_size
        with path.open("rb"):
            pass
    except OSError as exc:
        raise NotFoundError("artifact not found") from exc
    return ArtifactFile(path=path, size=size, content_type=content_type, disposition=disposition)


class ArtifactService:
    def __init__(self, manager: JobManagerPort):
        self.manager = manager

    def job_artifact(self, job_id: str, artifact_name: str, *, download: bool = False) -> ArtifactFile:
        with map_legacy_errors(not_found="job not found", api_status=404):
            path = self.manager.artifact(job_id, artifact_name)
        return _describe(Path(path), download=download)

    def batch_artifact(
        self, batch_id: str, artifact_name: str, *, download: bool = False
    ) -> ArtifactFile:
        with map_legacy_errors(not_found="batch not found", api_status=404):
            path = self.manager.batch_artifact(batch_id, artifact_name)
        return _describe(Path(path), download=download)
