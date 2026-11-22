"""Lifecycle/journal contracts. Every database is a disposable SQLite file."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from facebook_backend.domain.lifecycle import JobLifecycle, LifecycleError
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.lifecycle_events import EventKeyConflict, append_lifecycle_event, replay_job_events
from facebook_backend.persistence.models import JobEvent
from facebook_backend.persistence.repositories import EventRepository, JobRepository


PROOF = {"process_state": "stopped", "transport_cleanup_state": "restored"}
CONTEXT = {"attempt_id": "attempt-1", "worker_id": "worker-1", "device_serial": "test-device",
           "trace_id": "e" * 32}
JOB = "fb_fixture"
ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.url = f"sqlite+pysqlite:///{(self.root / 'test.db').as_posix()}"
        self.db = Database.open(self.url)
        self.addCleanup(self.db.dispose)
        self.db.create_all()
        with self.db.session() as session:
            JobRepository(session).create(job_id=JOB, status="queued", payload={})

    def write_event(self, kind, key, before, after, **kwargs):
        with self.db.session() as session:
            if before is not None and after != before:
                repo = JobRepository(session)
                current = repo.require(JOB)
                self.assertEqual(current["status"], before)
                repo.update(JOB, expected_version=current["version"], status=after)
            return append_lifecycle_event(session, job_id=JOB, event_type=kind, event_key=key,
                                          before=before, after=after, **kwargs)

    def created(self):
        return self.write_event("job.created", "create", None, "queued")

    def started(self):
        self.created()
        return self.write_event("job.claimed", "claim:1", "queued", "running", **CONTEXT)

    def replay(self):
        with self.db.session() as session:
            return replay_job_events(session, JOB)

    def test_full_sequence_replays_after_database_reopen(self):
        self.started()
        for kind, details in [("job.started", {}), ("job.progress", {"progress_count": 3}),
                              ("job.process_stopped", {"process_state": "stopped"}),
                              ("job.cleanup_completed", {"transport_cleanup_state": "restored"}),
                              ("job.artifact_verified", {"artifact_count": 2})]:
            self.write_event(kind, kind, "running", "running", payload=details, **CONTEXT)
        row = self.write_event("job.completed", "end:1", "running", "completed", payload=PROOF, **CONTEXT)
        self.db.dispose()
        with Database.open(self.url) as reopened, reopened.session() as session:
            result = replay_job_events(session, JOB)
            self.assertTrue(result.matches_projection)
            self.assertFalse(result.partial_history)
            self.assertEqual((result.state.status, result.event_count, result.last_event_id),
                             ("completed", 8, row.event_id))
            stored = session.get(JobEvent, row.event_id)
            self.assertEqual({key: getattr(stored, key) for key in CONTEXT}, CONTEXT)

    def test_idempotent_retry_after_later_events_and_conflicting_payload(self):
        self.started()
        args = dict(job_id=JOB, event_type="job.progress", event_key="progress:1", before="running",
                    after="running", **CONTEXT)
        with self.db.session() as session:
            original = append_lifecycle_event(session, **args, payload={"attempt": 1, "progress_count": 2})
        self.write_event("job.completed", "done", "running", "completed", payload=PROOF, **CONTEXT)
        with self.db.session() as session:
            repeated = append_lifecycle_event(session, **args, payload={"progress_count": 2, "attempt": 1})
            self.assertEqual((original.event_id, original.timestamp), (repeated.event_id, repeated.timestamp))
            for changes in ({"payload": {"progress_count": 3}}, {"trace_id": "f" * 32},
                            {"worker_id": "other"}, {"event_type": "job.shutdown"}):
                with self.subTest(changes=changes), self.assertRaises(EventKeyConflict):
                    append_lifecycle_event(session, **(args | {"payload": {"attempt": 1, "progress_count": 2}} | changes))
        self.assertEqual(self.replay().event_count, 4)

    def test_duplicate_key_cannot_mask_reversal_of_stored_status(self):
        self.started()
        self.write_event("job.completed", "done", "running", "completed", payload=PROOF, **CONTEXT)
        with self.assertRaises(LifecycleError), self.db.session() as session:
            JobRepository(session).update(JOB, status="running")
            append_lifecycle_event(session, job_id=JOB, event_type="job.claimed", event_key="claim:1",
                                   before="queued", after="running", **CONTEXT)
        self.assertTrue(self.replay().matches_projection)

    def test_replay_reads_one_snapshot_across_concurrent_commit(self):
        self.created()
        with self.db.session() as reader:
            first = replay_job_events(reader, JOB)
            self.write_event("job.claimed", "claim:1", "queued", "running", **CONTEXT)
            self.assertEqual(replay_job_events(reader, JOB), first)
            self.assertTrue(first.matches_projection)
        self.assertEqual(self.replay().state.status, "running")

    def test_job_and_event_roll_back_as_one_unit(self):
        self.created()
        with self.assertRaisesRegex(RuntimeError, "injected"), self.db.session() as session:
            JobRepository(session).update(JOB, expected_version=0, status="running")
            append_lifecycle_event(session, job_id=JOB, event_type="job.claimed", event_key="claim:1",
                                   before="queued", after="running", **CONTEXT)
            raise RuntimeError("injected after event flush")
        result = self.replay()
        self.assertEqual((result.stored_status, result.state.status, result.event_count), ("queued", "queued", 1))
        with self.assertRaises(LifecycleError), self.db.session() as session:
            append_lifecycle_event(session, job_id=JOB, event_type="job.claimed", event_key="claim:1",
                                   before="queued", after="running", **CONTEXT)

    def test_cancellation_wins_over_late_success_retry_and_failed_stop(self):
        self.started()
        self.write_event("job.cancellation_requested", "cancel:1", "running", "cancelling")
        self.write_event("job.shutdown", "shutdown:1", "cancelling", "cancelling")
        for kind, after, details in [("job.completed", "completed", PROOF), ("job.retry_scheduled", "queued", PROOF),
                                     ("job.failed", "failed", PROOF), ("job.recovered", "queued", PROOF),
                                     ("job.cancelled", "cancelled", {}),
                                     ("job.cancelled", "cancelled", PROOF | {"process_state": "not-started"})]:
            with self.subTest(kind=kind, details=details), self.assertRaises(LifecycleError):
                self.write_event(kind, "late", "cancelling", after, payload=details, **CONTEXT)
        self.write_event("job.cancelled", "stop:1", "cancelling", "cancelled", payload=PROOF, **CONTEXT)
        self.assertTrue(self.replay().matches_projection)
        self.assertEqual(self.replay().event_count, 5)

    def test_retry_requires_proof_and_fences_previous_attempt(self):
        self.started()
        with self.assertRaises(LifecycleError):
            self.write_event("job.retry_scheduled", "retry:1", "running", "queued", **CONTEXT)
        self.write_event("job.retry_scheduled", "retry:1", "running", "queued", payload=PROOF, **CONTEXT)
        with self.assertRaises(LifecycleError):
            self.write_event("job.claimed", "other-claim", "queued", "running", **CONTEXT)
        next_context = CONTEXT | {"attempt_id": "attempt-2"}
        self.write_event("job.claimed", "claim:2", "queued", "running", **next_context)
        with self.assertRaises(LifecycleError):
            self.write_event("job.completed", "late:1", "running", "completed", payload=PROOF, **CONTEXT)
        self.write_event("job.failed", "fail:2", "running", "failed", payload=PROOF, **next_context)
        with self.assertRaises(LifecycleError):
            self.write_event("job.retry_scheduled", "late:2", "failed", "queued", payload=PROOF, **next_context)
        self.assertEqual(self.replay().state.status, "failed")

    def test_shutdown_closes_dispatch_until_recovery(self):
        self.created()
        self.write_event("job.shutdown", "close", "queued", "queued")
        with self.assertRaisesRegex(LifecycleError, "dispatch is closed"):
            self.write_event("job.claimed", "claim:1", "queued", "running", **CONTEXT)
        self.write_event("job.recovered", "open", "queued", "queued")
        self.write_event("job.claimed", "claim:1", "queued", "running", **CONTEXT)
        self.assertTrue(self.replay().matches_projection)

    def test_worker_failure_and_reused_older_attempt_cannot_report_success(self):
        self.started()
        self.write_event("job.worker_failed", "fault:1", "running", "running", payload={"failure": "device"}, **CONTEXT)
        with self.assertRaisesRegex(LifecycleError, "failed attempt"):
            self.write_event("job.completed", "late:1", "running", "completed", payload=PROOF, **CONTEXT)
        self.write_event("job.retry_scheduled", "retry:1", "running", "queued", payload=PROOF, **CONTEXT)
        next_context = CONTEXT | {"attempt_id": "attempt-2"}
        self.write_event("job.claimed", "claim:2", "queued", "running", **next_context)
        self.write_event("job.failover", "retry:2", "running", "queued", payload=PROOF, **next_context)
        with self.assertRaisesRegex(LifecycleError, "new attempt"):
            self.write_event("job.claimed", "claim:3", "queued", "running", **CONTEXT)
        self.assertTrue(self.replay().matches_projection)

    def test_result_persistence_hold_survives_shutdown_and_recovery(self):
        self.started()
        self.write_event("job.result_write_failed", "storage-fault", "running", "running", **CONTEXT)
        self.write_event("job.shutdown", "shutdown", "running", "running")
        for event, status in (("job.recovered", "queued"), ("job.completed", "completed")):
            with self.assertRaises(LifecycleError):
                self.write_event(event, "late", "running", status, payload=PROOF, **CONTEXT)
        result = self.replay()
        self.assertEqual((result.state.status, result.state.result_write_failed), ("running", True))

    def test_unknown_types_and_unbounded_or_sensitive_fields_are_rejected(self):
        self.created()
        args = dict(job_id=JOB, event_type="job.queued", event_key="observation", before="queued", after="queued")
        changes = [{"event_type": "set-status"}, {"event_key": ""}, {"event_key": "https://some.where"},
                   {"trace_id": "0" * 32}, {"worker_id": "x" * 65}, {"trace_id": "wrong"},
                   {"payload": {"from": "running"}}, {"payload": {"url": "fixture://private"}},
                   {"payload": {"failure": {"value": "network"}}}, {"payload": {"attempt": True}},
                   {"payload": {"failure": "arbitrary error with sensitive text"}}]
        with self.db.session() as session:
            for change in changes:
                with self.subTest(change=change), self.assertRaises(LifecycleError):
                    append_lifecycle_event(session, **(args | change))
        self.assertEqual(self.replay().event_count, 1)

    def test_legacy_status_and_import_preserved_across_cutover(self):
        with self.db.session() as session:
            repo = EventRepository(session)
            repo.append(job_id=JOB, event_type="legacy-import", payload={"status": "queued", "source": "private-path"})
            repo.append(job_id=JOB, event_type="historical-note", payload={"arbitrary": "private-detail"})
            repo.append(job_id=JOB, event_type="job.status_changed", payload={"from": "queued", "to": "running"})
            JobRepository(session).update(JOB, status="running")
        self.write_event("job.recovered", "recovery:1", "running", "queued", payload=PROOF)
        result = self.replay()
        self.assertTrue(result.matches_projection)
        self.assertTrue(result.partial_history)
        self.assertEqual((result.legacy_count, result.ignored_legacy_count, result.state.status), (3, 1, "queued"))
        self.assertNotIn("private", repr(result))
        with self.db.session() as session:
            self.assertEqual(EventRepository(session).list_for_job(JOB)[0]["payload"]["source"], "private-path")

    def test_diagnosis_stops_at_corrupt_row_even_beyond_old_200_event_page(self):
        self.started()
        with self.db.session() as session:
            for index in range(205):
                session.add(JobEvent(job_id=JOB, event_type="other", payload="{}", timestamp=str(index)))
            session.flush()
            damaged = JobEvent(job_id=JOB, event_type="job.completed", event_key="corrupt",
                               payload='{"from":"queued","to":"completed"}', timestamp="1900")
            session.add(damaged)
            session.flush()
        result = self.replay()
        self.assertEqual((result.state.status, result.invalid_event_id, result.event_count),
                         ("running", damaged.event_id, 208))
        self.assertFalse(result.matches_projection)
        self.assertEqual(result.invalid_reason, "invalid lifecycle transition or stale previous status")
        with self.assertRaisesRegex(LifecycleError, "inconsistent"):
            self.write_event("job.progress", "next", "running", "running", **CONTEXT)

    def test_concurrent_redeliveries_commit_one_event_and_sql_unique_index(self):
        barrier = Barrier(4)

        def deliver(_):
            with self.db.session() as session:
                barrier.wait(timeout=10)
                return append_lifecycle_event(session, job_id=JOB, event_type="job.created", event_key="create",
                                              before=None, after="queued").event_id

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(len(set(pool.map(deliver, range(4)))), 1)
        self.assertEqual(self.replay().event_count, 1)
        with self.assertRaises(IntegrityError), self.db.session() as session:
            session.add(JobEvent(job_id=JOB, event_type="job.created", event_key="create", timestamp="x"))
            session.flush()

    def test_all_terminal_states_are_absorbing_even_on_shutdown(self):
        for status in ("completed", "failed", "cancelled"):
            state = JobLifecycle(status)
            self.assertEqual(state.advance("job.shutdown", before=status, after=status), state)
            for event in ("job.recovered", "job.retry_scheduled", "job.claimed", "job.shutdown"):
                with self.subTest(event=event, status=status), self.assertRaises(LifecycleError):
                    state.advance(event, before=status, after="queued")


class MigrationTests(unittest.TestCase):
    def test_legacy_bytes_survive_upgrade_downgrade_and_new_keys_block_lossy_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{(Path(directory) / 'migration.db').as_posix()}"
            config = Config()
            config.set_main_option("script_location", str(ROOT / "facebook_backend" / "persistence" / "migrations"))
            config.attributes["database_url"] = url
            command.upgrade(config, "0003_artifact_integrity")
            with Database.open(url) as database:
                with database.engine.begin() as conn:
                    conn.exec_driver_sql("INSERT INTO jobs(id,status,created_at,updated_at,payload) "
                                         "VALUES ('fb_fixture','queued','t','t','{}')")
                    for _ in range(2):
                        conn.exec_driver_sql("INSERT INTO job_events(job_id,event_type,timestamp,payload) "
                                             "VALUES ('fb_fixture','note','t','{  \"note\": 1 }')")
                command.upgrade(config, "0004_job_event_context")
                indexes = {i["name"]: i for i in inspect(database.engine).get_indexes("job_events")}
                self.assertEqual(indexes["uq_job_events_job_key"]["column_names"], ["job_id", "event_key"])
                self.assertTrue(indexes["uq_job_events_job_key"]["unique"])
                command.downgrade(config, "0003_artifact_integrity")
                self.assertNotIn("event_key", {c["name"] for c in inspect(database.engine).get_columns("job_events")})
                command.upgrade(config, "0004_job_event_context")
                with database.session() as session:
                    old = session.scalars(select(JobEvent).order_by(JobEvent.event_id)).all()
                    self.assertEqual([(r.event_id, r.payload, r.event_key) for r in old],
                                     [(1, '{  "note": 1 }', None), (2, '{  "note": 1 }', None)])
                    append_lifecycle_event(session, job_id=JOB, event_type="job.queued", event_key="queue",
                                           before="queued", after="queued")
                with self.assertRaisesRegex(RuntimeError, "pre-upgrade backup"):
                    command.downgrade(config, "0003_artifact_integrity")
                with database.session() as session:
                    self.assertTrue(replay_job_events(session, JOB).matches_projection)
