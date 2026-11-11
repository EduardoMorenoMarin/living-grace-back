"""QR creation contracts reuse the isolated rehearsal/JWT fixture."""
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import test_rehearsal_read as fixtures
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models import MinistryMember, MinistryMemberRole, RehearsalQrSession
from app.models.enums import MembershipStatus
from app.repositories.rehearsal_qr_session import RehearsalQrSessionRepository


class RehearsalQrSessionTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change
    setUp = fixtures.RehearsalReadTests.setUp

    def create(self, path_id=301, **changes):
        now = datetime.now(timezone.utc)
        body = {"opens_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat()}
        body.update(changes)
        return self.client.post(f"/rehearsals/{path_id}/qr-sessions", headers=self.headers, json=body)

    def rows(self):
        return list(self.db.scalars(select(RehearsalQrSession)))

    def test_creation_hash_response_and_no_raw_token_in_storage_or_logs(self):
        offset = timezone(timedelta(hours=-5))
        start = datetime.now(offset) + timedelta(minutes=10)
        end = start + timedelta(hours=1)
        with self.assertNoLogs(level="WARNING"):
            response = self.create(opens_at=start.isoformat(), expires_at=end.isoformat())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(set(body), {"id", "rehearsal_id", "check_in_url", "opens_at", "expires_at"})
        self.assertEqual(body["rehearsal_id"], 301)
        self.assertEqual(body["opens_at"], start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
        token = parse_qs(urlparse(body["check_in_url"]).query)["token"][0]
        self.assertGreaterEqual(len(token), 43)
        row = self.rows()[0]
        self.assertEqual(row.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(row.created_by, 101)
        stored = str(dict(self.db.execute(select(RehearsalQrSession.__table__)).mappings().one()))
        self.assertNotIn(token, stored)
        self.assertEqual(row.id, body["id"])

    def test_unauthenticated(self):
        self.headers = {}
        self.assertEqual(self.create().status_code, 401)
        self.assertEqual(self.rows(), [])

    def test_unauthorized_and_creator_without_privileges(self):
        for actor in (102, 104):
            self.login(actor)
            self.assertEqual(self.create().status_code, 403)
        self.login(103)  # Rehearsal creator still needs current administrative privileges.
        self.change(MinistryMemberRole, 504, revoked_at=datetime.now(timezone.utc))
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.rows(), [])

    def test_missing_and_outside_ministry(self):
        self.assertEqual(self.create(99999).status_code, 404)
        self.assertEqual(self.create(304).status_code, 404)
        self.change(MinistryMember, 201, status=MembershipStatus.INACTIVE)
        self.assertEqual(self.create().status_code, 403)

    def test_invalid_naive_expired_and_extra_fields(self):
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        for changes in (
            {"opens_at": "invalid"}, {"opens_at": "2030-01-01T18:00:00"},
            {"expires_at": "2030-01-01T19:00:00"}, {"opens_at": None},
            {"opens_at": future.isoformat(), "expires_at": future.isoformat()},
            {"opens_at": future.isoformat()},
            {"opens_at": "2000-01-01T18:00:00Z", "expires_at": "2000-01-01T19:00:00Z"},
            {"token_hash": "attacker"}, {"rehearsal_id": 302},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.create(**changes).status_code, 422)
        self.assertEqual(self.rows(), [])

    def test_duplicate_even_expired_and_revoked_allowed(self):
        self.assertEqual(self.create().status_code, 201)
        row = self.rows()[0]
        row.opens_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        row.expires_at = datetime(2000, 1, 2, tzinfo=timezone.utc)
        self.db.commit()
        self.assertEqual(self.create().status_code, 409)
        row.revoked_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(len(self.rows()), 2)

    def test_unique_index_handles_race_after_precheck(self):
        self.assertEqual(self.create().status_code, 201)
        # Simulate another transaction winning after the absence check. The real
        # INSERT hits the unique index and must roll back without exposing SQL.
        with patch.object(RehearsalQrSessionRepository, "has_non_revoked", return_value=False):
            response = self.create()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.rows()), 1)

    def test_postgresql_unique_violation_is_sanitized_and_rolled_back(self):
        class UniqueViolation(Exception):
            sqlstate = "23505"
        with patch.object(RehearsalQrSessionRepository, "add", side_effect=IntegrityError(
            "private SQL", {}, UniqueViolation("private constraint"),
        )):
            response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private", response.text)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.create().status_code, 201)

    def test_insert_and_commit_failures_do_not_return_token(self):
        for target, method in ((RehearsalQrSessionRepository, "add"), (type(self.db), "commit")):
            with patch.object(target, method, side_effect=SQLAlchemyError("private")):
                response = self.create()
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "Unable to create QR session."})
            self.assertEqual(self.rows(), [])

    def test_raw_token_not_passed_to_sql_or_logging(self):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            with patch("logging.Logger._log") as log:
                response = self.create()
            self.assertEqual(response.status_code, 201)
            token = parse_qs(urlparse(response.json()["check_in_url"]).query)["token"][0]
            self.assertNotIn(token, repr(statements))
            self.assertNotIn(token, repr(log.call_args_list))
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)


# Reuse the existing opt-in, committed scratch-PostgreSQL fixture. This never
# falls back to the application's Supabase database or creates any tables.
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import delete
from sqlalchemy.orm import Session
import test_rehearsal_delete_concurrency as concurrency_fixtures
from app.core.exceptions import RehearsalAttendanceConflict
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.schemas.rehearsal_qr_session import RehearsalQrSessionCreate
from app.services.rehearsal_qr_session import RehearsalQrSessionService


@unittest.skipUnless(os.environ.get("REHEARSAL_CONCURRENCY_DATABASE_URL"),
                     "opt-in scratch PostgreSQL concurrency tests")
class PostgreSQLQrSessionConcurrencyTests(unittest.TestCase):
    setUp = concurrency_fixtures.PostgreSQLRehearsalDeleteConcurrencyTests.setUp

    def cleanup_fixture(self):
        with Session(self.engine) as db:
            db.execute(delete(RehearsalQrSession).where(RehearsalQrSession.rehearsal_id == self.rehearsal_id))
            db.commit()
        concurrency_fixtures.PostgreSQLRehearsalDeleteConcurrencyTests.cleanup_fixture(self)

    def test_concurrent_creation_has_exactly_one_winner(self):
        barrier = Barrier(2)
        data = RehearsalQrSessionCreate(
            opens_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        def worker():
            with Session(self.engine, autoflush=False) as db:
                service = RehearsalQrSessionService(
                    RehearsalQrSessionRepository(db), RehearsalReadRepository(db), "https://example.com",
                )
                barrier.wait(timeout=5)
                try:
                    service.create(self.rehearsal_id, self.user_id, data)
                    return 201
                except RehearsalAttendanceConflict:
                    return 409
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=15) for f in futures), [201, 409])
        with Session(self.engine) as db:
            self.assertEqual(len(list(db.scalars(select(RehearsalQrSession).where(
                RehearsalQrSession.rehearsal_id == self.rehearsal_id,
            )))), 1)
