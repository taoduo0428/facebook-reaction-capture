"""Offline rollback never uses the service, devices or the working tree as state."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from facebook_backend.persistence import offline_rollback as rollback
from facebook_backend.persistence.database import Database, default_database_path
from facebook_backend.persistence.job_store import DatabaseJobStore, ManifestJobStore
from facebook_backend.persistence.manifest_export import rollback_from_sqlite
from facebook_backend.persistence.models import Base
from facebook_backend.persistence.state_startup import state_owner, _alembic_config
from alembic import command


class OfflineRollbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "installation"
        self.path = default_database_path(self.root)
        self.path.parent.mkdir(parents=True)
        self.dest = Path(temporary.name) / "new-target"
        self.url = f"sqlite+pysqlite:///{self.path.as_posix()}"
        config = _alembic_config()
        config.attributes['database_url'] = self.url
        command.upgrade(config, 'head')
        self.request = {"post_url": "https://www.facebook.com/fixture/posts/123"}
        self.database = Database.open(self.url)
        self.addCleanup(self.database.dispose)
        self.store = DatabaseJobStore(self.database)
        self.store.save_job({'job_id': 'fb_one', 'status': 'queued', 'request': self.request})

    def export(self):
        return rollback_from_sqlite(self.root, self.dest, database_url=self.url)

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute(query, args)

    def test_backup_from_wal_and_manifest_readback_preserve_old_files(self):
        self.store.save_job({'job_id': 'fb_two', 'status': 'queued', 'request': self.request})
        self.store.save_batch({'batch_id': 'batch_test', 'job_ids': ['fb_two', 'fb_one']})
        old = self.root / 'api-jobs' / 'fb_one.job.json'
        old.parent.mkdir()
        old.write_bytes(b'old manifest bytes')
        self.assertGreater(Path(str(self.path) + '-wal').stat().st_size, 0)
        result = self.export()
        self.assertTrue(result['ok'])
        with closing(sqlite3.connect(result['backup'])) as snapshot:
            self.assertEqual(snapshot.execute('select count(*) from jobs').fetchone(), (2,))
        target = ManifestJobStore(self.dest)
        self.assertEqual({i for _, i in target.load_job_manifests()}, {'fb_one', 'fb_two'})
        manifest, _ = next(target.load_batch_manifests())
        self.assertEqual(manifest['job_ids'], ['fb_two', 'fb_one'])
        self.assertEqual(old.read_bytes(), b'old manifest bytes')
        self.assertEqual({p.name for p in self.dest.iterdir()}, {'database.sqlite3', 'api-jobs', 'api-batches'})

    def test_writes_after_backup_do_not_change_exported_snapshot(self):
        backup = rollback._backup
        def concurrent_commit(source, target):
            backup(source, target)
            self.sql("UPDATE jobs SET status='cancelled'")
        with patch.object(rollback, '_backup', side_effect=concurrent_commit):
            result = self.export()
        job, _ = next(ManifestJobStore(self.dest).load_job_manifests())
        self.assertEqual(job['status'], 'queued')
        with closing(sqlite3.connect(result['backup'])) as con:
            self.assertEqual(con.execute('select status from jobs').fetchone()[0], job['status'])

    def test_owner_exclusion(self):
        with state_owner(self.root, self.url), self.assertRaises(RuntimeError):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_destination_race_preserves_existing_empty_directory(self):
        publish = rollback._publish
        def competitor(stage, target):
            target.mkdir()
            publish(stage, target)
        with patch.object(rollback, '_publish', side_effect=competitor), self.assertRaises(OSError):
            self.export()
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(list(self.dest.parent.glob('.manifest-rollback-*')), [])

    def test_rejected_destination_or_mid_write_failure(self):
        for dest in [self.root, self.root / 'data' / 'output', self.path]:
            with self.subTest(dest=dest), self.assertRaises(ValueError):
                rollback_from_sqlite(self.root, dest, database_url=self.url)
        with patch.object(ManifestJobStore, 'load_job_manifests', return_value=[]), self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.dest.exists())
        with patch.object(rollback.os, 'fsync', side_effect=OSError('disk failure')), self.assertRaises(OSError):
            self.export()
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.glob('.manifest-rollback-*')), [])

    def test_pin_result_and_unresolved_execution_or_cleanup_fail_closed(self):
        for field, value in [('pinned_device_id', 'device'), ('result', '{}'), ('status', 'running'),
                             ('status', 'cancelling'), ('process_state', 'stop-unverified'), ('process_pid', 123),
                             ('transport_cleanup_state', 'pending'), ('transport_cleanup_state', 'runner-owned'),
                             ('recovery_state', 'failed-result-persistence'), ('status', 'completed')]:
            with self.subTest(field=field, value=value):
                self.sql(f'UPDATE jobs SET {field}=?', (value,))
                with self.assertRaises(ValueError):
                    self.export()
                self.assertFalse(self.dest.exists())
                self.sql(f'UPDATE jobs SET {field}=?', ('queued' if field == 'status' else None,))

    def test_active_attempt_and_lease_block(self):
        self.sql("INSERT INTO job_attempts(attempt_id,job_id) VALUES('a','fb_one')")
        with self.assertRaisesRegex(ValueError, 'unfinished attempt'):
            self.export()
        self.sql("UPDATE job_attempts SET finished_at='now'")
        self.sql("INSERT INTO devices(id,worker_id,serial,updated_at) VALUES('d','w','s','now')")
        self.sql("INSERT INTO device_leases(device_id,lease_token,acquired_at,heartbeat_at,expires_at) "
                 "VALUES('d', 'token', 'then', 'then', 'past')")
        with self.assertRaisesRegex(ValueError, 'open device lease'):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_input_identifier_and_run_paths_confined(self):
        self.sql("DELETE FROM job_events")
        self.sql("UPDATE jobs SET id='fb_../../escaped'")
        with self.assertRaisesRegex(ValueError, 'identifier'):
            self.export()
        self.sql("UPDATE jobs SET id='fb_one'")
        for candidate in ('../outside', 'runs/../../escape', str(self.dest), 'C:drive-relative', 'runs/with:stream'):
            self.sql('UPDATE jobs SET run_dir=?', (candidate,))
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.export()
        self.assertFalse(self.dest.exists())

    def test_cli_then_environment_then_default_database(self):
        other = self.root / 'invalid.db'
        with patch.dict(os.environ, {"FACEBOOK_DB_URL": f'sqlite:///{other.as_posix()}'}):
            self.assertEqual(rollback._database_path(self.root, self.url), self.path)
            with self.assertRaises(FileNotFoundError):
                rollback._database_path(self.root, None)
        with patch.dict(os.environ, {"FACEBOOK_DB_URL": ''}):
            self.assertEqual(rollback._database_path(self.root, None), self.path)
        self.assertFalse(other.exists())
        for url in ['sqlite:///:memory:', 'postgresql://host/db', self.url + '?uri=true']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                rollback._database_path(self.root, url)

    def test_invalid_schema_and_broken_membership(self):
        self.sql('UPDATE jobs SET batch_index=1')
        with self.assertRaisesRegex(ValueError, 'membership'):
            self.export()
        self.sql("UPDATE alembic_version SET version_num='unknown'")
        with self.assertRaises(RuntimeError):
            self.export()
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()
