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
        folder = Path(temporary.name).resolve()
        self.root = folder / "installation"
        self.path = default_database_path(self.root)
        self.path.parent.mkdir(parents=True)
        self.dest = folder / "new-target"
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

    def completed_fixture(self):
        import facebook_capture_api as api

        run = self.root / 'runs' / 'fixture'
        analysis = run / 'analysis'
        analysis.mkdir(parents=True)
        report = {'target_verification': {'status': 'matched', 'matched_post_id': '123'},
                  'source_post_ids_by_family': {'reactions': ['123']}, 'statuses': {'200': 1},
                  'reaction_integrity': {'query_records': 1, 'terminal_page_seen': True,
                                         'completion_claim_allowed': True, 'successful_page_count': 1,
                                         'raw_reactor_rows': 1, 'unique_reactor_identities': 1}}
        (analysis / 'facebook-sample-patterns.json').write_text(json.dumps(report), encoding='utf-8')
        users = analysis / 'facebook-reactor-users.csv'
        users.write_text('user_id,user_name,source_post_id\n01,Fixture,123\n', encoding='utf-8')
        (run / 'capture.log').write_bytes(b'synthetic')
        request = api.normalize_request(dict(self.request, collect=['reactions']))
        result = api.build_api_result(run, request)
        result['job_log'] = str(run / 'capture.log')
        self.store.save_job({'job_id': 'fb_one', 'status': 'completed', 'request': request,
                             'run_dir': 'runs/fixture', 'process_state': 'stopped', 'finished_at': 'fixture',
                             'transport_cleanup_state': 'runner-owned', 'completion_scope': result['completion_scope'],
                             'result': result})
        return users, result

    def test_completed_result_roundtrip_through_operator_cli_and_legacy_manager(self):
        import io
        from contextlib import redirect_stdout
        import facebook_capture_api as api
        from facebook_backend.persistence import legacy_manifest_import as cli

        _, original = self.completed_fixture()
        output = io.StringIO()
        flags = ['--root', str(self.root), '--rollback', '--rollback-dir', str(self.dest)]
        with patch.dict(os.environ, {'FACEBOOK_DB_URL': self.url}), patch.object(cli, '_service_is_running', return_value=False), redirect_stdout(output):
            self.assertEqual(cli.main(flags), 0, output.getvalue())
        self.assertTrue(json.loads(output.getvalue())['ok'])
        with patch.object(api, 'BASE_DIR', self.root):
            manager = api.JobManager(store=ManifestJobStore(self.dest))
            self.assertEqual(manager.result('fb_one'), original)

    def test_completed_result_requires_identical_verified_evidence(self):
        users, _ = self.completed_fixture()
        users.write_text('user_id,user_name,source_post_id\n01,Different,123\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.export()
        users.write_text('user_id,user_name,source_post_id\n01,Fixture,999\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'validator'):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_mid_export_evidence_change_detected(self):
        users, _ = self.completed_fixture()
        write = rollback._write_and_check
        def mutate(*args):
            write(*args)
            users.write_bytes(b'concurrent external edit')
        with patch.object(rollback, '_write_and_check', side_effect=mutate), self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.export()
        self.assertFalse(self.dest.exists())

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
