"""Artifact discovery, metadata and the download path boundary.

The path check is the security-relevant part and is moved verbatim: logical
artifact names are a fixed whitelist, the relative path may not be absolute or
contain ``..``, a symlinked component is refused, the resolved candidate must
stay inside the run directory, and the file must be non-empty.  It is *not* a
sandbox against a hostile local writer — that was never the claim.

Integrity is the existing standard: the required artifacts must exist and be
non-empty.  There is no content hash here; the database phase adds ``sha256`` as
metadata without changing the pass/fail rule.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from facebook_backend.artifacts.catalog import (
    ARTIFACT_GROUP_BY_NAME,
    ARTIFACT_PATHS,
    ARTIFACT_SPECS,
    DEFAULT_SAVE_ARTIFACTS,
    INLINE_ARTIFACT_SUFFIXES,
    REQUIRED_ARTIFACTS,
)
from facebook_backend.domain.exceptions import ApiError


def checked_artifact_path(
    root: Path,
    relative: Path,
    *,
    unavailable: str = "artifact is not available yet",
) -> Path:
    """Constrain artifacts below a trusted root without changing logical names.

    The root may be relocated. Child symbolic links and escaping directory
    junctions are not artifact files.
    """
    relative = Path(relative)
    if relative.anchor or ".." in relative.parts:
        raise ApiError("artifact path is outside its run directory")
    candidate = root / relative
    try:
        resolved_root = root.resolve(strict=True)
        current = root
        linked = False
        for part in relative.parts:
            current = current / part
            linked = linked or current.is_symlink()
        resolved_candidate = candidate.resolve(strict=True)
        confined = resolved_candidate.is_relative_to(resolved_root)
    except (OSError, RuntimeError) as exc:
        raise ApiError(unavailable) from exc
    if linked or not confined:
        raise ApiError("artifact path is outside its run directory or linked")
    try:
        nonempty_file = candidate.is_file() and candidate.stat().st_size > 0
    except OSError as exc:
        raise ApiError(unavailable) from exc
    if not nonempty_file:
        raise ApiError(unavailable)
    return candidate


def artifact_metadata(
    run_dir: Path,
    job_status: str,
    save_artifacts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-artifact state used by the progress snapshot and the UI."""
    artifacts: list[dict[str, Any]] = []
    selected_groups = set(save_artifacts if save_artifacts is not None else DEFAULT_SAVE_ARTIFACTS)
    for name, label, layer, relative in ARTIFACT_SPECS:
        path = run_dir / Path(relative)
        group = ARTIFACT_GROUP_BY_NAME.get(name)
        selected = name in REQUIRED_ARTIFACTS or group in selected_groups
        try:
            checked = checked_artifact_path(run_dir, Path(relative))
            size = checked.stat().st_size
        except (ApiError, OSError):
            size = 0
        if not selected:
            state = "discarding" if job_status in {"queued", "running", "cancelling"} else "not_saved"
            size = 0
        elif size > 0:
            state = "ready" if job_status in {"completed", "failed", "cancelled"} else "writing"
        else:
            state = "missing" if job_status == "completed" else "waiting"
        artifacts.append(
            {
                "name": name,
                "label": label,
                "layer": layer,
                "path": str(path),
                "size_bytes": size,
                "state": state,
                "viewable": path.suffix.lower() in INLINE_ARTIFACT_SUFFIXES,
                "url": f"/v1/jobs/{{job_id}}/artifacts/{name}",
                "required": name in REQUIRED_ARTIFACTS,
                "selected": selected,
            }
        )
    return artifacts


def required_artifacts_ready(run_dir: Path) -> bool:
    """The unchanged completion rule: required artifacts exist and are non-empty."""
    for name in REQUIRED_ARTIFACTS:
        path = run_dir / ARTIFACT_PATHS[name]
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


__all__ = [
    "artifact_metadata",
    "checked_artifact_path",
    "required_artifacts_ready",
]
