"""Register digests of files the current native attempt actually published."""
from __future__ import annotations

import logging
from pathlib import Path

from facebook_backend.artifacts.catalog import ARTIFACT_GROUP_BY_NAME, DEFAULT_SAVE_ARTIFACTS
from facebook_backend.persistence.repositories import JobRepository
from facebook_backend.scheduler.queue import write_session


LOGGER = logging.getLogger(__name__)


def register_publications(tx, claim, published):
    """Hash outside the write transaction; missing integrity data stays unknown."""
    if not published:
        return

    a = claim.assignment
    selected = set(a.request.get('save_artifacts', DEFAULT_SAVE_ARTIFACTS))
    root = Path(claim.job['run_dir'])
    for name in sorted(published):
        group = ARTIFACT_GROUP_BY_NAME.get(name)
        if group is not None and group not in selected:
            continue
        try:
            from facebook_backend.artifacts.integrity import prepare_publication, persist_publication
            digest = prepare_publication(root, 'job', name, complete=True, atomically_published=True)
            if digest is None:
                continue
            with write_session(tx.database) as session:
                current = JobRepository(session).require(a.job_id)
                if (current['status'] != 'completed' or current['attempt_count'] != a.attempt
                        or current['capture_run_dir'] != a.run_dir):
                    return
                persist_publication(session, 'job', a.job_id, name, digest)
        except Exception as exc:
            # This optional ledger is separate from the committed result/lease.
            # Log the type, never a host path, URL or digest input.
            LOGGER.warning('artifact metadata unavailable: %s', type(exc).__name__)
