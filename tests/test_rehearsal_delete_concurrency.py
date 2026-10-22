"""PostgreSQL races using committed, self-cleaning fixtures in a scratch DB.

Set REHEARSAL_CONCURRENCY_DATABASE_URL only to a disposable database with the
existing schema and an active GLOBAL ADMIN role. Never fall back to DATABASE_URL:
independent connections require committed fixtures, unlike rollback-only tests.
"""
import os
import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

# Import the existing fixture module for safe application/test defaults only.
import test_rehearsal_read
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RehearsalAttendanceConflict
from app.models import Ministry, MinistryMember, MinistryMemberRole, Rehearsal, RehearsalAttendance, Role, User
from app.models.enums import AttendanceStatus, MembershipStatus, RoleScope, RoleStatus
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.rehearsal_delete import RehearsalDeleteService


@unittest.skipUnless(os.environ.get("REHEARSAL_CONCURRENCY_DATABASE_URL"), "opt-in scratch PostgreSQL concurrency tests")
class PostgreSQLRehearsalDeleteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            os.environ["REHEARSAL_CONCURRENCY_DATABASE_URL"], isolation_level="READ COMMITTED",
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=8000 -c lock_timeout=5000"},
        )
        self.addCleanup(self.engine.dispose)
        self.assertEqual(self.engine.dialect.name, "postgresql")
        self.ids = [secrets.randbelow(9223372036854775807) + 1 for _ in range(8)]
        self.user_id, self.ministry_id, self.member_id, self.participant_id, self.rehearsal_id, self.attendance_id, self.role_assignment_id, self.new_attendance_id = self.ids
        self.addCleanup(self.cleanup_fixture)
        with Session(self.engine) as db:
            role_id = db.scalar(select(Role.id).where(
                Role.name == "ADMIN", Role.scope == RoleScope.GLOBAL,
                Role.status == RoleStatus.ACTIVE, Role.deleted_at.is_(None),
            ))
            self.assertIsNotNone(role_id, "Scratch database needs its existing ADMIN reference role.")
            unique = uuid4().hex
            db.add(User(id=self.user_id, email=f"delete-race-{unique}@example.com",
                        password_hash="unused", first_name="Race", last_name="Test"))
            db.add(Ministry(id=self.ministry_id, name=f"Delete race {unique}"))
            db.flush()
            db.add_all([
                MinistryMember(id=self.member_id, ministry_id=self.ministry_id, user_id=self.user_id, status=MembershipStatus.ACTIVE),
                # Same user can belong only once to a ministry. Use another user
                # for the selected participant so a fresh assignment can race too.
            ])
            second_user_id = self.participant_id
            db.add(User(id=second_user_id, email=f"participant-{unique}@example.com",
                        password_hash="unused", first_name="Participant", last_name="Test"))
            db.flush()
            db.add(MinistryMember(id=self.participant_id, ministry_id=self.ministry_id,
                                  user_id=second_user_id, status=MembershipStatus.ACTIVE))
            db.flush()
            db.add(MinistryMemberRole(id=self.role_assignment_id, ministry_member_id=self.member_id, role_id=role_id))
            start = datetime.now(timezone.utc)
            db.add(Rehearsal(id=self.rehearsal_id, ministry_id=self.ministry_id, created_by=self.user_id,
                             name="Delete race", start_at=start, end_at=start + timedelta(hours=1)))
            db.flush()
            db.add(RehearsalAttendance(id=self.attendance_id, rehearsal_id=self.rehearsal_id,
                                       ministry_member_id=self.participant_id))
            db.commit()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True, cancel_futures=True)

    def cleanup_fixture(self):
        with Session(self.engine) as db:
            db.execute(delete(RehearsalAttendance).where(RehearsalAttendance.rehearsal_id == self.rehearsal_id))
            db.execute(delete(Rehearsal).where(Rehearsal.id == self.rehearsal_id))
            db.execute(delete(MinistryMemberRole).where(MinistryMemberRole.id == self.role_assignment_id))
            db.execute(delete(MinistryMember).where(MinistryMember.id.in_([self.member_id, self.participant_id])))
            db.execute(delete(User).where(User.id.in_([self.user_id, self.participant_id])))
            db.execute(delete(Ministry).where(Ministry.id == self.ministry_id))
            db.commit()

    def delete_worker(self, ready=None, state=None, repository_type=RehearsalRepository):
        with Session(self.engine, autoflush=False) as db:
            if state is not None:
                state["pid"] = db.scalar(select(func.pg_backend_pid()))
            if ready is not None:
                ready.set()
            service = RehearsalDeleteService(repository_type(db), RehearsalReadRepository(db))
            try:
                service.delete(self.rehearsal_id, self.user_id)
            except RehearsalAttendanceConflict as exc:
                return str(exc)
        return "deleted"

    def recording_worker(self, ready, state, insert=False):
        with Session(self.engine) as db:
            state["pid"] = db.scalar(select(func.pg_backend_pid()))
            ready.set()
            try:
                if insert:
                    db.add(RehearsalAttendance(
                        id=self.new_attendance_id, rehearsal_id=self.rehearsal_id,
                        ministry_member_id=self.member_id, arrival_at=datetime.now(timezone.utc),
                    ))
                    db.commit()
                    return "inserted"
                result = db.execute(update(RehearsalAttendance).where(
                    RehearsalAttendance.id == self.attendance_id,
                ).values(arrival_at=datetime.now(timezone.utc), attendance_status=AttendanceStatus.ON_TIME))
                db.commit()
                return result.rowcount
            except IntegrityError as exc:
                db.rollback()
                return exc.orig.sqlstate

    def assert_blocked_by(self, waiting_pid, blocking_pid):
        deadline = time.monotonic() + 4
        with self.engine.connect() as observer:
            while time.monotonic() < deadline:
                if observer.scalar(text("SELECT :blocker = ANY(pg_blocking_pids(:waiting))"),
                                   {"blocker": blocking_pid, "waiting": waiting_pid}):
                    return
                time.sleep(0.02)
        self.fail("Expected PostgreSQL row-lock wait was not observed.")

    def assert_deleted(self):
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Rehearsal, self.rehearsal_id))
            self.assertEqual(list(db.scalars(select(RehearsalAttendance.id).where(
                RehearsalAttendance.rehearsal_id == self.rehearsal_id,
            ))), [])

    def test_parent_lock_alone_does_not_block_recording_and_check_refreshes_cached_child(self):
        with Session(self.engine, autoflush=False) as db:
            RehearsalReadRepository(db).get_admin_for_update(self.user_id, self.rehearsal_id)
            cached = db.get(RehearsalAttendance, self.attendance_id)
            self.assertIsNone(cached.arrival_at)
            ready, state = Event(), {}
            writer = self.pool.submit(self.recording_worker, ready, state)
            # The recorder completes while the parent FOR UPDATE is still held.
            self.assertEqual(writer.result(timeout=4), 1)
            self.assertIsNone(cached.arrival_at)
            with self.assertRaisesRegex(RehearsalAttendanceConflict, "Cannot delete a rehearsal with recorded attendance"):
                RehearsalDeleteService(RehearsalRepository(db), RehearsalReadRepository(db)).delete(
                    self.rehearsal_id, self.user_id,
                )
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(RehearsalAttendance, self.attendance_id).arrival_at)
            self.assertIsNotNone(db.get(Rehearsal, self.rehearsal_id))

    def test_recording_wins_delete_waits_then_rejects(self):
        with Session(self.engine) as recorder:
            recorder_pid = recorder.scalar(select(func.pg_backend_pid()))
            recorder.execute(update(RehearsalAttendance).where(RehearsalAttendance.id == self.attendance_id).values(
                attendance_status=AttendanceStatus.ON_TIME,
            ))
            ready, state = Event(), {}
            deletion = self.pool.submit(self.delete_worker, ready, state)
            try:
                self.assertTrue(ready.wait(2))
                self.assert_blocked_by(state["pid"], recorder_pid)
                recorder.commit()
            finally:
                recorder.rollback()
            self.assertEqual(deletion.result(timeout=10), "Cannot delete a rehearsal with recorded attendance.")
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(Rehearsal, self.rehearsal_id))
            self.assertEqual(db.get(RehearsalAttendance, self.attendance_id).attendance_status, AttendanceStatus.ON_TIME)

    def deletion_first(self, insert):
        locked, release, state = Event(), Event(), {}

        class PausingRepository(RehearsalRepository):
            def get_attendance_for_update(repository, rehearsal_id):
                rows = super().get_attendance_for_update(rehearsal_id)
                locked.set()
                if not release.wait(5):
                    raise SQLAlchemyError("Test synchronization timed out.")
                return rows

        deletion = self.pool.submit(self.delete_worker, None, state, PausingRepository)
        try:
            self.assertTrue(locked.wait(2))
            ready, recorder_state = Event(), {}
            recorder = self.pool.submit(self.recording_worker, ready, recorder_state, insert)
            self.assertTrue(ready.wait(2))
            self.assert_blocked_by(recorder_state["pid"], state["pid"])
        finally:
            release.set()
        self.assertEqual(deletion.result(timeout=10), "deleted")
        self.assertEqual(recorder.result(timeout=10), "23503" if insert else 0)
        self.assert_deleted()

    def test_delete_wins_recorder_waits_and_updates_zero_rows(self):
        self.deletion_first(insert=False)

    def test_new_recorded_assignment_cannot_bypass_parent_lock(self):
        self.deletion_first(insert=True)

    def test_real_postgresql_cascade_removes_unrecorded_assignments(self):
        self.assertEqual(self.delete_worker(), "deleted")
        self.assert_deleted()
