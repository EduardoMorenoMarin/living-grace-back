"""Check-in contracts reuse real SQLite queries, JWTs and rehearsal fixtures."""
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_rehearsal_read as fixtures
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import set_committed_value

from app.models import Ministry, MinistryMember, Rehearsal, RehearsalAttendance, RehearsalQrSession, User
from app.models.enums import AttendanceStatus, MembershipStatus, MinistryStatus, UserStatus
from app.repositories.attendance_check_in import AttendanceCheckInRepository


class AttendanceCheckInTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.now = datetime(2030, 1, 1, 18, tzinfo=timezone.utc)
        self.token = "a-secret-QR-token"
        self.change(Rehearsal, 301, start_at=self.now - timedelta(minutes=10),
                    end_at=self.now + timedelta(hours=1), tolerance_minutes=10)
        self.db.add(RehearsalQrSession(
            id=801, rehearsal_id=301, created_by=101,
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
            opens_at=self.now - timedelta(minutes=20), expires_at=self.now + timedelta(hours=1),
        ))
        self.db.commit()
        self.login(104)  # selected participant with an unset placeholder
        self.stack.enter_context(patch("app.services.attendance_check_in.utc_now", return_value=self.now))
        original = AttendanceCheckInRepository._locked
        def sqlite_datetimes(repository, model, *conditions):
            row = original(repository, model, *conditions)
            if row is not None:
                for column in model.__table__.columns:
                    value = getattr(row, column.name)
                    if isinstance(value, datetime) and value.tzinfo is None:
                        set_committed_value(row, column.name, value.replace(tzinfo=timezone.utc))
            return row
        self.stack.enter_context(patch.object(AttendanceCheckInRepository, "_locked", autospec=True,
                                             side_effect=sqlite_datetimes))

    def check_in(self, **body):
        return self.client.post("/attendance/check-in", headers=self.headers,
                                json=body or {"token": self.token})

    def attendance(self, member_id=206):
        self.db.expire_all()
        return self.db.scalar(select(RehearsalAttendance).where(
            RehearsalAttendance.rehearsal_id == 301,
            RehearsalAttendance.ministry_member_id == member_id,
        ))

    def test_new_record_returns_201_and_expected_structure(self):
        self.login(101)  # active ministry member with no selected attendance row
        response = self.check_in()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"rehearsal_id": 301, "attendance_status": "ON_TIME",
                                           "arrival_at": "2030-01-01T18:00:00Z"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.attendance(201).attendance_status, AttendanceStatus.ON_TIME)

    def test_null_placeholder_and_exact_cutoff_are_on_time(self):
        response = self.check_in()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["attendance_status"], "ON_TIME")
        self.assertEqual(self.attendance().id, 402)

    def test_one_microsecond_after_cutoff_is_late(self):
        self.change(Rehearsal, 301, start_at=self.now - timedelta(minutes=10, microseconds=1))
        response = self.check_in()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["attendance_status"], "LATE")

    def test_absent_placeholder_updated(self):
        self.change(RehearsalAttendance, 402, attendance_status=AttendanceStatus.ABSENT)
        self.assertEqual(self.check_in().status_code, 200)
        self.assertEqual(self.attendance().attendance_status, AttendanceStatus.ON_TIME)

    def test_recorded_statuses_or_arrival_preserved(self):
        for status, arrival in ((AttendanceStatus.ON_TIME, None), (AttendanceStatus.LATE, None),
                                (AttendanceStatus.JUSTIFIED, None), (None, self.now),
                                (AttendanceStatus.ABSENT, self.now)):
            self.change(RehearsalAttendance, 402, attendance_status=status, arrival_at=arrival)
            response = self.check_in()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.attendance().attendance_status, status)
            self.assertEqual(self.attendance().arrival_at is None, arrival is None)

    def test_repeated_check_in_conflicts(self):
        self.assertEqual(self.check_in().status_code, 200)
        before = self.attendance().arrival_at
        self.assertEqual(self.check_in().status_code, 409)
        self.assertEqual(self.attendance().arrival_at, before)

    def test_invalid_revoked_expired_and_unopened_share_generic_error(self):
        expected = {"detail": "Invalid or unavailable attendance session."}
        response = self.check_in(token="unknown-sensitive-token")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), expected)
        for changes in ({"revoked_at": self.now}, {"opens_at": self.now + timedelta(seconds=1)},
                        {"expires_at": self.now}, {"expires_at": self.now - timedelta(seconds=1)}):
            self.change(RehearsalQrSession, 801, opens_at=self.now - timedelta(minutes=20),
                        expires_at=self.now + timedelta(hours=1), revoked_at=None)
            self.change(RehearsalQrSession, 801, **changes)
            response = self.check_in()
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json(), expected)
        self.assertIsNone(self.attendance().arrival_at)

    def test_opening_boundary_allowed(self):
        self.change(RehearsalQrSession, 801, opens_at=self.now)
        self.assertEqual(self.check_in().status_code, 200)

    def test_authentication_required(self):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            self.headers = headers
            self.assertEqual(self.check_in().status_code, 401)
        self.assertIsNone(self.attendance().arrival_at)

    def test_inactive_deleted_or_missing_membership_and_user(self):
        for model, identifier, field, value in (
            (MinistryMember, 206, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 206, "status", MembershipStatus.PENDING),
            (MinistryMember, 206, "deleted_at", self.now),
            (User, 104, "status", UserStatus.INACTIVE),
            (User, 104, "deleted_at", self.now),
            (Ministry, 11, "status", MinistryStatus.INACTIVE),
            (Ministry, 11, "deleted_at", self.now),
        ):
            previous = getattr(self.db.get(model, identifier), field)
            self.change(model, identifier, **{field: value})
            self.assertEqual(self.check_in().status_code, 403)
            self.change(model, identifier, **{field: previous})
        self.change(MinistryMember, 206, ministry_id=12)
        self.assertEqual(self.check_in().status_code, 403)
        self.assertIsNone(self.attendance().arrival_at)

    def test_invalid_request_does_not_echo_token_or_allow_manual_values(self):
        for body in ({"token": ""}, {"token": None}, {"token": 42},
                     {"token": self.token, "arrival_at": self.now.isoformat()},
                     {"token": self.token, "attendance_status": "ON_TIME"},
                     {"token": self.token, "user_id": 101}):
            response = self.check_in(**body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.token, response.text)

    def test_expiration_during_lock_wait_or_write_rolls_back(self):
        expiration = self.now + timedelta(hours=1)
        for times in ((self.now, expiration), (self.now, self.now, expiration)):
            with patch("app.services.attendance_check_in.utc_now", side_effect=times):
                response = self.check_in()
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIsNone(self.attendance().arrival_at)

    def test_persistence_failure_rolls_back_and_sanitizes(self):
        for target, method in ((AttendanceCheckInRepository, "record"), (type(self.db), "commit")):
            with patch.object(target, method, side_effect=SQLAlchemyError("private data")):
                response = self.check_in()
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("private", response.text)
            self.assertIsNone(self.attendance().arrival_at)

    def test_token_never_persisted_passed_to_sql_or_logged(self):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            with patch("logging.Logger._log") as log:
                response = self.check_in()
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(self.token, response.text)
            self.assertNotIn(self.token, repr(statements))
            self.assertNotIn(self.token, repr(log.call_args_list))
            self.assertIn(hashlib.sha256(self.token.encode()).hexdigest(), repr(statements))
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

    def test_unique_pair_constraint_handles_competing_insert(self):
        # Force the stale precheck seen when another writer wins the INSERT.
        with patch.object(AttendanceCheckInRepository, "get_attendance", return_value=None):
            response = self.check_in()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNone(self.attendance().arrival_at)


import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from sqlalchemy import delete, func, update
from sqlalchemy.orm import Session
import test_rehearsal_delete_concurrency as concurrency_fixtures
from app.core.exceptions import AttendanceSessionInvalid, RehearsalAttendanceConflict
from app.schemas.attendance_check_in import AttendanceCheckIn
from app.services.attendance_check_in import AttendanceCheckInService


@unittest.skipUnless(os.environ.get("REHEARSAL_CONCURRENCY_DATABASE_URL"),
                     "opt-in scratch PostgreSQL concurrency tests")
class PostgreSQLCheckInConcurrencyTests(unittest.TestCase):
    setUp = concurrency_fixtures.PostgreSQLRehearsalDeleteConcurrencyTests.setUp
    assert_blocked_by = concurrency_fixtures.PostgreSQLRehearsalDeleteConcurrencyTests.assert_blocked_by

    def cleanup_fixture(self):
        with Session(self.engine) as db:
            db.execute(delete(RehearsalQrSession).where(RehearsalQrSession.rehearsal_id == self.rehearsal_id))
            db.commit()
        concurrency_fixtures.PostgreSQLRehearsalDeleteConcurrencyTests.cleanup_fixture(self)

    def prepare_qr(self):
        self.token = "concurrency-check-in-token-" + str(self.rehearsal_id)
        with Session(self.engine) as db:
            now = datetime.now(timezone.utc)
            qr = RehearsalQrSession(
                rehearsal_id=self.rehearsal_id, created_by=self.user_id,
                token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
                opens_at=now - timedelta(minutes=5), expires_at=now + timedelta(hours=1),
            )
            db.add(qr)
            db.flush()
            self.qr_id = qr.id
            db.commit()

    def worker(self, barrier=None, ready=None, state=None, user_id=None):
        with Session(self.engine, autoflush=False) as db:
            if state is not None:
                state["pid"] = db.scalar(select(func.pg_backend_pid()))
            if ready:
                ready.set()
            if barrier:
                barrier.wait(timeout=5)
            try:
                _, created = AttendanceCheckInService(AttendanceCheckInRepository(db)).check_in(
                    user_id or self.user_id, AttendanceCheckIn(token=self.token),
                )
                return 201 if created else 200
            except RehearsalAttendanceConflict:
                return 409
            except AttendanceSessionInvalid:
                return 400

    def test_concurrent_new_rows_have_one_winner(self):
        self.prepare_qr()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, barrier) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=15) for f in futures), [201, 409])
        with Session(self.engine) as db:
            rows = list(db.scalars(select(RehearsalAttendance).where(
                RehearsalAttendance.rehearsal_id == self.rehearsal_id,
                RehearsalAttendance.ministry_member_id == self.member_id,
            )))
            self.assertEqual(len(rows), 1)

    def test_concurrent_absent_updates_have_one_winner(self):
        self.prepare_qr()
        with Session(self.engine) as db:
            db.execute(update(RehearsalAttendance).where(RehearsalAttendance.id == self.attendance_id)
                       .values(attendance_status=AttendanceStatus.ABSENT))
            db.commit()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, barrier, user_id=self.participant_id) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=15) for f in futures), [200, 409])

    def test_revocation_committed_during_wait_is_seen(self):
        self.prepare_qr()
        with Session(self.engine) as revoker:
            pid = revoker.scalar(select(func.pg_backend_pid()))
            revoker.execute(update(RehearsalQrSession).where(RehearsalQrSession.id == self.qr_id)
                            .values(revoked_at=datetime.now(timezone.utc)))
            ready, state = Event(), {}
            future = self.pool.submit(self.worker, ready=ready, state=state)
            try:
                self.assertTrue(ready.wait(2))
                self.assert_blocked_by(state["pid"], pid)
                revoker.commit()
            finally:
                revoker.rollback()
            self.assertEqual(future.result(timeout=10), 400)

    def test_expiration_committed_during_wait_is_seen(self):
        self.prepare_qr()
        with Session(self.engine) as writer:
            pid = writer.scalar(select(func.pg_backend_pid()))
            writer.execute(update(RehearsalQrSession).where(RehearsalQrSession.id == self.qr_id)
                           .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            ready, state = Event(), {}
            future = self.pool.submit(self.worker, ready=ready, state=state)
            try:
                self.assertTrue(ready.wait(2))
                self.assert_blocked_by(state["pid"], pid)
                writer.commit()
            finally:
                writer.rollback()
            self.assertEqual(future.result(timeout=10), 400)
