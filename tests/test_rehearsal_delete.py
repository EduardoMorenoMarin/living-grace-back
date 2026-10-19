"""DELETE HTTP contracts use isolated SQLite and the existing retrieval fixture."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import test_rehearsal_read as fixtures
from app.core.config import settings
from app.models import Ministry, MinistryMember, MinistryMemberRole, Rehearsal, RehearsalAttendance, Role
from app.models.enums import AttendanceStatus, MembershipStatus, MinistryStatus, RoleStatus
from app.repositories.rehearsal import RehearsalRepository
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError


class RehearsalDeleteTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    get = fixtures.RehearsalReadTests.get
    change = fixtures.RehearsalReadTests.change
    setUp = fixtures.RehearsalReadTests.setUp

    def delete(self, rehearsal_id=303, **params):
        return self.client.delete(f"/rehearsals/{rehearsal_id}", headers=self.headers, params=params)

    def snapshot(self):
        return (
            [dict(row) for row in self.db.execute(select(Rehearsal.__table__).order_by(Rehearsal.id)).mappings()],
            [dict(row) for row in self.db.execute(select(RehearsalAttendance.__table__).order_by(RehearsalAttendance.id)).mappings()],
        )

    def test_admin_deletes_empty_rehearsal_and_repeated_delete_is_404(self):
        response = self.delete(305)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.get(305).status_code, 404)
        self.assertEqual(self.delete(305).status_code, 404)

    def test_deletion_cascades_only_target_attendance(self):
        before = self.snapshot()
        # Load the relationship too: passive_deletes='all' must still let the FK
        # cascade instead of ORM attempts to NULL the nonnullable foreign key.
        self.assertEqual(len(self.db.get(Rehearsal, 303).rehearsal_attendance), 1)
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            response = self.delete()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.get(303).status_code, 404)
        self.assertEqual(self.delete().status_code, 404)
        self.assertEqual(self.snapshot(), (
            [row for row in before[0] if row["id"] != 303],
            [row for row in before[1] if row["rehearsal_id"] != 303],
        ))
        deletes = [sql for sql in statements if sql.lstrip().upper().startswith("DELETE")]
        self.assertEqual(len(deletes), 1)
        self.assertIn("DELETE FROM rehearsals", deletes[0])

    def test_user_or_no_role_cannot_delete_or_spoof_ids(self):
        before = self.snapshot()
        for actor in (102, 104):
            self.login(actor)
            self.assertEqual(self.delete(user_id=101, role="ADMIN").status_code, 403)
        self.assertEqual(before, self.snapshot())

    def test_missing_invalid_and_expired_jwt(self):
        import jwt
        before = self.snapshot()
        expired = jwt.encode({"sub": "101", "exp": 1}, settings.JWT_SECRET_KEY.get_secret_value(),
                             algorithm=settings.JWT_ALGORITHM)
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}):
            self.assertEqual(self.client.delete("/rehearsals/303", headers=headers).status_code, 401)
        self.assertEqual(before, self.snapshot())

    def test_nonexistent_outside_ministry_or_invalid_id(self):
        before = self.snapshot()
        self.assertEqual(self.delete(99999).status_code, 404)
        self.assertEqual(self.delete(304).status_code, 404)
        for identifier in (0, -1, "invalid", "1.5", 9223372036854775808):
            self.assertEqual(self.delete(identifier).status_code, 422)
        self.assertEqual(before, self.snapshot())

    def test_invalid_admin_membership_and_ministry_do_not_grant_scope(self):
        for model, identifier, field, value in (
            (MinistryMember, 202, "status", MembershipStatus.PENDING),
            (MinistryMember, 202, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 202, "deleted_at", datetime.now(timezone.utc)),
            (Ministry, 12, "status", MinistryStatus.INACTIVE),
            (Ministry, 12, "deleted_at", datetime.now(timezone.utc)),
        ):
            original = getattr(self.db.get(model, identifier), field)
            self.change(model, identifier, **{field: value})
            before = self.snapshot()
            self.assertEqual(self.delete(302).status_code, 404)
            self.assertEqual(before, self.snapshot())
            self.change(model, identifier, **{field: original})

    def test_revoked_inactive_or_deleted_admin_role_denied(self):
        for model, identifier, field, value in (
            (MinistryMemberRole, 501, "revoked_at", datetime.now(timezone.utc)),
            (Role, 17, "status", RoleStatus.INACTIVE),
            (Role, 17, "deleted_at", datetime.now(timezone.utc)),
        ):
            original = getattr(self.db.get(model, identifier), field)
            self.change(model, identifier, **{field: value})
            before = self.snapshot()
            self.assertEqual(self.delete().status_code, 403)
            self.assertEqual(before, self.snapshot())
            self.change(model, identifier, **{field: original})

    def test_arrival_or_status_alone_blocks_deletion_without_changes(self):
        for arrival, status in ((datetime.now(timezone.utc), None), (None, AttendanceStatus.ABSENT)):
            self.change(RehearsalAttendance, 404, arrival_at=arrival, attendance_status=status)
            before = self.snapshot()
            response = self.delete()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"detail": "Cannot delete a rehearsal with recorded attendance."})
            self.assertEqual(before, self.snapshot())

    def test_one_recorded_participant_blocks_entire_deletion(self):
        before = self.snapshot()
        response = self.delete(301)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "Cannot delete a rehearsal with recorded attendance."})
        self.assertEqual(before, self.snapshot())

    def test_partial_delete_and_commit_failures_roll_back_cascade(self):
        original = RehearsalRepository.delete

        def fail_after_delete(repository, rehearsal):
            original(repository, rehearsal)
            raise SQLAlchemyError("private database failure")

        before = self.snapshot()
        with patch.object(RehearsalRepository, "delete", autospec=True, side_effect=fail_after_delete):
            response = self.delete()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to delete rehearsal."})
        self.assertEqual(before, self.snapshot())
        with patch.object(type(self.db), "commit", side_effect=SQLAlchemyError("private commit failure")):
            self.assertEqual(self.delete().status_code, 500)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.delete().status_code, 204)

    def test_lock_read_failure_fails_closed(self):
        before = self.snapshot()
        with patch.object(RehearsalRepository, "get_attendance_for_update", side_effect=SQLAlchemyError("private")):
            self.assertEqual(self.delete().status_code, 500)
        self.assertEqual(before, self.snapshot())

    def test_delete_cors_preflight(self):
        response = self.client.options("/rehearsals/303", headers={
            "Origin": settings.FRONTEND_ORIGIN, "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "Authorization",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("DELETE", response.headers["access-control-allow-methods"])
