"""Artifact integrity baselines (Phase H).

The checks here are the ones that make a SHA-256 useful rather than decorative:
it is only taken for a file the writer declared final, it is streamed, polling
does not re-hash, and a later change is reported instead of silently accepted.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from facebook_backend.artifacts import integrity
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import ArtifactIntegrity, Base

CSV = "facebook-reactor-users.csv"


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "analysis").mkdir()
        self.path = self.root / "analysis" / CSV
        self.path.write_bytes(b"user_id,name\n1,alice\n2,bob\n")
        self.database = Database.open(f"sqlite+pysqlite:///{(self.root / 'a.db').as_posix()}")
        self.addCleanup(self.database.dispose)
        Base.metadata.create_all(self.database.engine)

    def publish(self, **kwargs):
        return integrity.prepare_publication(self.root, "job", CSV, complete=True, atomically_published=True, **kwargs)

    def test_streaming_digest_matches_hashlib(self):
        digest = self.publish()
        self.assertIsNotNone(digest)
        self.assertEqual(digest.sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(digest.size, self.path.stat().st_size)
        # The MIME guess comes from the OS table (Windows maps .csv to
        # application/vnd.ms-excel), so assert it is populated, not its value.
        self.assertTrue(digest.content_type)

    def test_large_file_is_hashed_in_chunks_not_loaded(self):
        big = self.root / "analysis" / CSV
        with big.open("wb") as handle:
            for _ in range(8):
                handle.write(b"x" * (integrity.CHUNK // 2))
        calls = {"n": 0}
        real = hashlib.sha256

        class Counter:
            def __init__(self):
                self._inner = real()

            def update(self, block):
                calls["n"] += 1
                self._inner.update(block)

            def hexdigest(self):
                return self._inner.hexdigest()

        with mock.patch.object(integrity.hashlib, "sha256", Counter):
            digest = self.publish()
        self.assertIsNotNone(digest)
        self.assertGreater(calls["n"], 1, "the file must be read in more than one chunk")

    def test_incomplete_or_unpublished_files_get_no_baseline(self):
        self.assertIsNone(integrity.prepare_publication(self.root, "job", CSV, complete=False, atomically_published=True))
        self.assertIsNone(integrity.prepare_publication(self.root, "job", CSV, complete=True, atomically_published=False))

    def test_empty_and_missing_files_get_no_baseline(self):
        self.path.write_bytes(b"")
        self.assertIsNone(self.publish())
        self.path.unlink()
        self.assertIsNone(self.publish())

    def test_symlink_escape_is_rejected(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: outside.exists() and __import__("shutil").rmtree(outside, ignore_errors=True))
        target = outside / "secret.csv"
        target.write_bytes(b"user_id\n1\n")
        self.path.unlink()
        try:
            os.symlink(target, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        with self.assertRaises(integrity.IntegrityError):
            self.publish()

    def test_persist_and_verify_round_trip(self):
        digest = self.publish()
        with self.database.session() as session:
            integrity.persist_publication(session, "job", "fb_1", CSV, digest)
        with self.database.session() as session:
            row = session.get(ArtifactIntegrity, ("job", "fb_1", CSV))
            record = {"owner_kind": "job", "artifact_name": CSV, "sha256": row.sha256,
                      "size": row.size, "file_stamp": row.file_stamp}
        self.assertEqual(integrity.verify_artifact(self.root, "job", CSV, record), integrity.VERIFIED)

    def test_republishing_updates_the_same_row(self):
        first = self.publish()
        with self.database.session() as session:
            integrity.persist_publication(session, "job", "fb_1", CSV, first)
        self.path.write_bytes(b"user_id\n9\n")
        second = self.publish()
        with self.database.session() as session:
            integrity.persist_publication(session, "job", "fb_1", CSV, second)
            count = session.query(ArtifactIntegrity).count()
        self.assertEqual(count, 1)
        self.assertNotEqual(first.sha256, second.sha256)

    def test_verify_uses_the_stamp_without_rehashing(self):
        digest = self.publish()
        record = {"owner_kind": "job", "artifact_name": CSV, "sha256": digest.sha256,
                  "size": digest.size, "file_stamp": digest.stamp}
        with mock.patch.object(integrity, "sha256_file", side_effect=AssertionError("must not rehash")):
            self.assertEqual(integrity.verify_artifact(self.root, "job", CSV, record), integrity.VERIFIED)

    def test_changed_and_missing_files_are_reported(self):
        digest = self.publish()
        record = {"owner_kind": "job", "artifact_name": CSV, "sha256": digest.sha256,
                  "size": digest.size, "file_stamp": digest.stamp}
        self.path.write_bytes(b"user_id\n1\n2\n3\n4\n5\n")
        self.assertEqual(integrity.verify_artifact(self.root, "job", CSV, record), integrity.CHANGED)
        self.path.unlink()
        self.assertEqual(integrity.verify_artifact(self.root, "job", CSV, record), integrity.MISSING)

    def test_report_counts_each_outcome(self):
        digest = self.publish()
        records = [
            {"owner_kind": "job", "artifact_name": CSV, "sha256": digest.sha256,
             "size": digest.size, "file_stamp": digest.stamp},
            {"owner_kind": "job", "artifact_name": "facebook-sample-patterns.json",
             "sha256": "0" * 64, "size": 3, "file_stamp": "x"},
        ]
        report = integrity.integrity_report(self.root, records)
        self.assertEqual(report["counts"][integrity.VERIFIED], 1)
        self.assertEqual(report["counts"][integrity.MISSING], 1)
        self.assertFalse(report["ok"])

    def test_batch_artifacts_are_supported(self):
        (self.root / "batch-summary.json").write_bytes(b'{"batch_id":"b"}\n')
        digest = integrity.prepare_publication(
            self.root, "batch", "batch-summary.json", complete=True, atomically_published=True
        )
        self.assertIsNotNone(digest)
        with self.database.session() as session:
            integrity.persist_publication(session, "batch", "batch_1", "batch-summary.json", digest)
            self.assertIsNotNone(session.get(ArtifactIntegrity, ("batch", "batch_1", "batch-summary.json")))


    def test_batch_publications_register_through_the_production_helper(self):
        from facebook_backend.persistence.repositories import BatchRepository
        from facebook_backend.scheduler.production_publications import register_batch_publications

        run_dir = self.root / "runs" / "batch_1"
        run_dir.mkdir(parents=True)
        (run_dir / "batch-summary.json").write_bytes(b'{"batch_id":"batch_1"}\n')
        with self.database.session() as session:
            BatchRepository(session).create(batch_id="batch_1", status="completed", run_dir=str(run_dir))

        class Tx:
            database = self.database

        register_batch_publications(Tx(), "batch_1")
        with self.database.session() as session:
            rows = session.query(ArtifactIntegrity).filter_by(owner_kind="batch", owner_id="batch_1").all()
            names = {row.artifact_name for row in rows}
        self.assertEqual(names, {"batch-summary.json"})


if __name__ == "__main__":
    unittest.main()
