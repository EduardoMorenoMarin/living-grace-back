"""Metadata/revocation contracts with existing isolated rehearsal/JWT fixtures."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_attendance_check_in as check_in_fixtures
import test_rehearsal_qr_session as creation_fixtures
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Ministry, MinistryMember, MinistryMemberRole, RehearsalQrSession, Role
from app.models.enums import MembershipStatus, MinistryStatus, RoleStatus
from app.repositories.rehearsal_qr_session import RehearsalQrSessionRepository
from app.repositories.rehearsal_read import RehearsalReadRepository


class QrSessionManagementTests(unittest.TestCase):
    login = check_in_fixtures.AttendanceCheckInTests.login
    change = check_in_fixtures.AttendanceCheckInTests.change
    check_in = check_in_fixtures.AttendanceCheckInTests.check_in
    attendance = check_in_fixtures.AttendanceCheckInTests.attendance
    create = creation_fixtures.RehearsalQrSessionTests.create

    def setUp(self):
        check_in_fixtures.AttendanceCheckInTests.setUp(self)
        self.login(101)
        self.stack.enter_context(patch("app.services.rehearsal_qr_session.utc_now", return_value=self.now))
        original = RehearsalQrSessionRepository.get_active_metadata
        def sqlite_datetimes(repository, *args):
            row = original(repository, *args)
            if row is None:
                return None
            # SQLite drops timezone metadata; production PostgreSQL preserves it.
            return {key: value.replace(tzinfo=timezone.utc) if isinstance(value, datetime)
                    and value.tzinfo is None else value for key, value in row.items()}
        self.stack.enter_context(patch.object(RehearsalQrSessionRepository, "get_active_metadata",
                                             autospec=True, side_effect=sqlite_datetimes))

    def manage(self, method="GET", rehearsal_id=301):
        return self.client.request(method, f"/rehearsals/{rehearsal_id}/qr-sessions/active",
                                   headers=self.headers)

    def snapshot(self):
        return [dict(row) for row in self.db.execute(select(RehearsalQrSession.__table__)
                                                    .order_by(RehearsalQrSession.id)).mappings()]

    def test_get_metadata_only_and_does_not_write(self):
        before = self.snapshot()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            response = self.manage()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"id", "rehearsal_id", "opens_at", "expires_at", "created_at"})
        self.assertEqual(body["id"], 801)
        self.assertEqual(body["rehearsal_id"], 301)
        self.assertEqual(body["opens_at"], "2030-01-01T17:40:00Z")
        self.assertEqual(body["expires_at"], "2030-01-01T19:00:00Z")
        self.assertTrue(body["created_at"].endswith("Z"))
        self.assertNotIn(self.token, response.text)
        self.assertNotIn("token_hash", " ".join(statements))
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.snapshot(), before)

    def test_get_missing_expired_scheduled_and_revoked(self):
        self.assertEqual(self.manage(rehearsal_id=302).status_code, 404)
        for changes in ({"expires_at": self.now}, {"expires_at": self.now - timedelta(seconds=1)},
                        {"opens_at": self.now + timedelta(seconds=1)}, {"revoked_at": self.now}):
            self.change(RehearsalQrSession, 801, opens_at=self.now - timedelta(minutes=20),
                        expires_at=self.now + timedelta(hours=1), revoked_at=None)
            self.change(RehearsalQrSession, 801, **changes)
            before = self.snapshot()
            self.assertEqual(self.manage().status_code, 404)
            self.assertEqual(self.snapshot(), before)

    def test_get_opening_boundary_inclusive(self):
        self.change(RehearsalQrSession, 801, opens_at=self.now)
        self.assertEqual(self.manage().status_code, 200)

    def test_revoke_preserves_row_and_all_other_fields(self):
        before = self.snapshot()[0]
        response = self.manage("DELETE")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        after = self.snapshot()
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["revoked_at"].replace(tzinfo=timezone.utc), self.now)
        self.assertEqual({key: value for key, value in after[0].items() if key != "revoked_at"},
                         {key: value for key, value in before.items() if key != "revoked_at"})
        self.assertEqual(self.manage().status_code, 404)

    def test_repeated_or_missing_revocation_returns_404(self):
        self.assertEqual(self.manage("DELETE", 302).status_code, 404)
        self.assertEqual(self.manage("DELETE").status_code, 204)
        before = self.snapshot()
        self.assertEqual(self.manage("DELETE").status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_expired_and_scheduled_sessions_can_be_revoked(self):
        for changes in ({"expires_at": self.now}, {"opens_at": self.now + timedelta(seconds=1)}):
            self.change(RehearsalQrSession, 801, opens_at=self.now - timedelta(minutes=20),
                        expires_at=self.now + timedelta(hours=1), revoked_at=None)
            self.change(RehearsalQrSession, 801, **changes)
            self.assertEqual(self.manage("DELETE").status_code, 204)

    def test_revocation_allows_replacement_creation(self):
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.manage("DELETE").status_code, 204)
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotEqual(response.json()["id"], 801)
        self.assertEqual(len(self.snapshot()), 2)

    def test_revoked_token_rejected_by_real_check_in_endpoint(self):
        self.assertEqual(self.manage("DELETE").status_code, 204)
        self.login(104)
        response = self.check_in()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json(), {"detail": "Invalid or unavailable attendance session."})
        self.assertNotIn(self.token, response.text)
        self.assertIsNone(self.attendance().arrival_at)

    def test_authentication_and_roles_required_for_both_methods(self):
        before = self.snapshot()
        for method in ("GET", "DELETE"):
            for headers in ({}, {"Authorization": "Bearer invalid"}):
                self.headers = headers
                self.assertEqual(self.manage(method).status_code, 401)
            for user_id in (102, 104):
                self.login(user_id)
                self.assertEqual(self.manage(method).status_code, 403)
            self.login(103)  # creator still requires a current ADMIN assignment
            self.change(MinistryMemberRole, 504, revoked_at=self.now)
            self.assertEqual(self.manage(method).status_code, 403)
            self.change(MinistryMemberRole, 504, revoked_at=None)
        self.assertEqual(self.snapshot(), before)

    def test_missing_outside_ministry_and_unavailable_membership(self):
        for method in ("GET", "DELETE"):
            for identifier in (99999, 304):
                self.assertEqual(self.manage(method, identifier).status_code, 404)
            self.change(MinistryMember, 202, status=MembershipStatus.INACTIVE)
            self.assertEqual(self.manage(method, 302).status_code, 404)
            self.change(MinistryMember, 202, status=MembershipStatus.ACTIVE)
            self.change(Ministry, 11, status=MinistryStatus.INACTIVE)
            # ADMIN assignment comes from ministry 11, so authorization fails closed.
            self.assertEqual(self.manage(method).status_code, 403)
            self.change(Ministry, 11, status=MinistryStatus.ACTIVE)
            self.change(Role, 17, status=RoleStatus.INACTIVE)
            self.assertEqual(self.manage(method).status_code, 403)
            self.change(Role, 17, status=RoleStatus.ACTIVE)

    def test_read_and_revoke_failures_are_sanitized_and_rollback(self):
        before = self.snapshot()
        for target, method in ((RehearsalReadRepository, "get_visible"),
                               (RehearsalQrSessionRepository, "get_active_metadata")):
            with patch.object(target, method, side_effect=SQLAlchemyError("private details")):
                response = self.manage()
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "Unable to retrieve QR session."})
        original = RehearsalQrSessionRepository.revoke
        def fail_after_write(repository, *args):
            original(repository, *args)
            raise SQLAlchemyError("private write details")
        with patch.object(RehearsalQrSessionRepository, "revoke", autospec=True, side_effect=fail_after_write):
            response = self.manage("DELETE")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to revoke QR session."})
        self.assertEqual(self.snapshot(), before)
        with patch.object(type(self.db), "commit", side_effect=SQLAlchemyError("private commit")):
            self.assertEqual(self.manage("DELETE").status_code, 500)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.manage("DELETE").status_code, 204)


import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.exceptions import RehearsalQrSessionNotFound
from app.schemas.rehearsal_qr_session import RehearsalQrSessionCreate
from app.services.rehearsal_qr_session import RehearsalQrSessionService
from app.services.attendance_check_in import AttendanceCheckInService
from app.repositories.attendance_check_in import AttendanceCheckInRepository
from app.schemas.attendance_check_in import AttendanceCheckIn
from app.core.exceptions import AttendanceSessionInvalid


@unittest.skipUnless(os.environ.get("REHEARSAL_CONCURRENCY_DATABASE_URL"),
                     "opt-in scratch PostgreSQL concurrency tests")
class PostgreSQLQrManagementConcurrencyTests(unittest.TestCase):
    setUp = check_in_fixtures.PostgreSQLCheckInConcurrencyTests.setUp
    prepare_qr = check_in_fixtures.PostgreSQLCheckInConcurrencyTests.prepare_qr
    cleanup_fixture = check_in_fixtures.PostgreSQLCheckInConcurrencyTests.cleanup_fixture
    assert_blocked_by = check_in_fixtures.PostgreSQLCheckInConcurrencyTests.assert_blocked_by

    def service(self, db, repository_type=RehearsalQrSessionRepository):
        return RehearsalQrSessionService(repository_type(db), RehearsalReadRepository(db), "https://example.com")

    def revoke_worker(self, barrier=None, repository_type=RehearsalQrSessionRepository):
        with Session(self.engine, autoflush=False) as db:
            if barrier:
                barrier.wait(timeout=5)
            try:
                self.service(db, repository_type).revoke_active(self.rehearsal_id, self.user_id)
                return 204
            except RehearsalQrSessionNotFound:
                return 404

    def test_concurrent_revocations_have_one_winner(self):
        self.prepare_qr()
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.revoke_worker, barrier) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=15) for f in futures), [204, 404])

    def hold_revocation(self):
        locked, release, state = Event(), Event(), {}
        class PausingRepository(RehearsalQrSessionRepository):
            def revoke(repository, session, now):
                super().revoke(session, now)
                state["pid"] = repository.db.scalar(select(func.pg_backend_pid()))
                locked.set()
                if not release.wait(5):
                    raise SQLAlchemyError("Test synchronization timed out.")
        future = self.pool.submit(self.revoke_worker, repository_type=PausingRepository)
        self.assertTrue(locked.wait(2))
        self.addCleanup(release.set)
        return future, release, state

    def test_create_waits_for_revocation_then_succeeds(self):
        self.prepare_qr()
        revocation, release, state = self.hold_revocation()
        ready, creator = Event(), {}
        def create_worker():
            with Session(self.engine, autoflush=False) as db:
                creator["pid"] = db.scalar(select(func.pg_backend_pid()))
                ready.set()
                now = datetime.now(timezone.utc)
                return self.service(db).create(self.rehearsal_id, self.user_id, RehearsalQrSessionCreate(
                    opens_at=now, expires_at=now + timedelta(hours=1),
                ))
        creation = self.pool.submit(create_worker)
        try:
            self.assertTrue(ready.wait(2))
            self.assert_blocked_by(creator["pid"], state["pid"])
        finally:
            release.set()
        self.assertEqual(revocation.result(timeout=10), 204)
        replacement = creation.result(timeout=10)
        self.assertNotEqual(replacement.id, self.qr_id)
        with Session(self.engine) as db:
            rows = list(db.scalars(select(RehearsalQrSession).where(
                RehearsalQrSession.rehearsal_id == self.rehearsal_id,
                RehearsalQrSession.revoked_at.is_(None),
            )))
            self.assertEqual([row.id for row in rows], [replacement.id])

    def test_check_in_waits_for_revocation_then_rejects_token(self):
        self.prepare_qr()
        revocation, release, state = self.hold_revocation()
        ready, attendee = Event(), {}
        def check_in_worker():
            with Session(self.engine, autoflush=False) as db:
                attendee["pid"] = db.scalar(select(func.pg_backend_pid()))
                ready.set()
                try:
                    AttendanceCheckInService(AttendanceCheckInRepository(db)).check_in(
                        self.user_id, AttendanceCheckIn(token=self.token),
                    )
                    return 201
                except AttendanceSessionInvalid:
                    return 400
        check_in = self.pool.submit(check_in_worker)
        try:
            self.assertTrue(ready.wait(2))
            self.assert_blocked_by(attendee["pid"], state["pid"])
        finally:
            release.set()
        self.assertEqual(revocation.result(timeout=10), 204)
        self.assertEqual(check_in.result(timeout=10), 400)

    def test_get_during_revocation_reads_committed_metadata_without_writes(self):
        self.prepare_qr()
        revocation, release, _ = self.hold_revocation()
        try:
            with Session(self.engine, autoflush=False) as db:
                metadata = self.service(db).get_active(self.rehearsal_id, self.user_id)
                self.assertEqual(metadata.id, self.qr_id)
        finally:
            release.set()
        self.assertEqual(revocation.result(timeout=10), 204)
        with Session(self.engine, autoflush=False) as db:
            with self.assertRaises(RehearsalQrSessionNotFound):
                self.service(db).get_active(self.rehearsal_id, self.user_id)
