"""PATCH contracts reuse the isolated rehearsal retrieval fixture."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import jwt
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import set_committed_value

import test_rehearsal_read as fixtures
from app.core.config import settings
from app.models import Ministry, MinistryMember, MinistryMemberRole, Rehearsal, RehearsalAttendance, Role, User
from app.models.enums import AttendanceStatus, MembershipStatus, MinistryStatus, RoleStatus, UserStatus
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository


class RehearsalUpdateTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    get = fixtures.RehearsalReadTests.get
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        original = RehearsalReadRepository.get_admin_for_update

        def sqlite_datetimes(repository, *args):
            rehearsal = original(repository, *args)
            # SQLite drops timezone metadata. Simulate PostgreSQL's aware result
            # for interval comparisons; all partial-date cases below use UTC.
            if rehearsal is not None:
                for field in ("start_at", "end_at"):
                    value = getattr(rehearsal, field)
                    if value.tzinfo is None:
                        set_committed_value(rehearsal, field, value.replace(tzinfo=timezone.utc))
            return rehearsal

        self.stack.enter_context(patch.object(
            RehearsalReadRepository, "get_admin_for_update", autospec=True, side_effect=sqlite_datetimes,
        ))

    def update(self, rehearsal_id=301, **data):
        return self.client.patch(f"/rehearsals/{rehearsal_id}", headers=self.headers, json=data)

    def snapshot(self):
        return (
            [dict(row) for row in self.db.execute(select(Rehearsal.__table__).order_by(Rehearsal.id)).mappings()],
            [dict(row) for row in self.db.execute(select(RehearsalAttendance.__table__).order_by(RehearsalAttendance.id)).mappings()],
        )

    def test_admin_updates_name_returns_same_detail_as_get(self):
        before = self.snapshot()
        response = self.update(name="  Updated rehearsal  ")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["name"], "Updated rehearsal")
        self.assertEqual(body, self.get(301).json())
        self.assertEqual(body["created_by"], {"id": 103, "first_name": "OtherAdmin", "last_name": "Last"})
        after = self.snapshot()
        self.assertEqual(before[1], after[1])
        old = before[0][0]
        new = after[0][0]
        self.assertGreater(new["updated_at"], old["updated_at"])
        for field in ("created_at", "created_by", "ministry_id", "id"):
            self.assertEqual(old[field], new[field])
        self.assertEqual(body["participants"][0]["functions"], ["Teclado", "Vocalista"])

    def test_multiple_fields_and_description_null(self):
        response = self.update(name="Changed", description="New description", tolerance_minutes=10,
                               start_at="2026-10-25T19:00:00Z", end_at="2026-10-25T21:00:00Z")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["description"], "New description")
        self.assertEqual(body["tolerance_minutes"], 10)
        self.assertTrue(body["start_at"].startswith("2026-10-25T19:00:00"))
        self.assertTrue(body["end_at"].startswith("2026-10-25T21:00:00"))
        self.assertEqual(self.update(description=None).json()["description"], None)

    def test_start_or_end_only_uses_stored_other_endpoint(self):
        for data in ({"start_at": "2026-10-25T19:00:00Z"}, {"end_at": "2026-10-25T21:00:00Z"}):
            response = self.update(**data)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.get(301).json()["start_at"].startswith("2026-10-25T19:00:00"))
        self.assertTrue(self.get(301).json()["end_at"].startswith("2026-10-25T21:00:00"))

    def test_invalid_merged_intervals_rollback(self):
        before = self.snapshot()
        for data in (
            {"start_at": "2026-10-25T20:00:00Z"}, {"end_at": "2026-10-25T18:00:00Z"},
            {"start_at": "2026-10-25T21:00:00Z"}, {"end_at": "2026-10-25T17:00:00Z"},
            {"start_at": "2026-10-26T20:00:00Z", "end_at": "2026-10-25T20:00:00Z"},
        ):
            with self.subTest(data=data):
                response = self.update(name="Must rollback", **data)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(before, self.snapshot())

    def test_invalid_unknown_protected_or_empty_input(self):
        invalid = [
            {}, {"name": "  "}, {"name": None}, {"description": 123}, {"start_at": None},
            {"end_at": None}, {"start_at": "invalid"}, {"end_at": "2026-10-25T21:00:00"},
            {"tolerance_minutes": -1}, {"tolerance_minutes": True}, {"tolerance_minutes": "1"},
            {"tolerance_minutes": 2147483648}, {"tolerance_minutes": None},
            {"ministry_member_ids": None}, {"ministry_member_ids": [203, 203]},
        ]
        invalid += [{field: value} for field, value in (
            ("id", 302), ("ministry_id", 12), ("created_by", 101), ("created_at", "2026-01-01"),
            ("updated_at", "2026-01-01"), ("role", "ADMIN"), ("unknown", 1),
        )]
        invalid += [{"ministry_member_ids": [value]} for value in (0, -1, True, "203", 1.5, None, 9223372036854775808)]
        before = self.snapshot()
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.update(**data).status_code, 422)
                self.assertEqual(before, self.snapshot())
        for identifier in (0, -1, "invalid", 9223372036854775808):
            self.assertEqual(self.update(identifier, name="Test").status_code, 422)

    def test_users_without_admin_cannot_edit_even_with_claims(self):
        before = self.snapshot()
        for actor in (102, 104):
            self.login(actor)
            self.assertEqual(self.update(name="Unauthorized").status_code, 403)
        token = jwt.encode({"sub": "102", "exp": 9999999999, "role": "ADMIN", "user_id": 101},
                           settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.update(name="Unauthorized").status_code, 403)
        self.assertEqual(before, self.snapshot())

    def test_missing_invalid_expired_jwt(self):
        before = self.snapshot()
        expired = jwt.encode({"sub": "101", "exp": 1}, settings.JWT_SECRET_KEY.get_secret_value(),
                             algorithm=settings.JWT_ALGORITHM)
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}):
            response = self.client.patch("/rehearsals/301", headers=headers, json={"name": "Unauthorized"})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(before, self.snapshot())

    def test_inaccessible_or_missing_returns_404(self):
        before = self.snapshot()
        self.assertEqual(self.update(304, name="Unauthorized").status_code, 404)
        self.assertEqual(self.update(99999, name="Missing").status_code, 404)
        self.change(MinistryMember, 202, status=MembershipStatus.INACTIVE)
        self.assertEqual(self.update(302, name="Inactive membership").status_code, 404)
        self.change(MinistryMember, 202, status=MembershipStatus.ACTIVE, deleted_at=datetime.now(timezone.utc))
        self.assertEqual(self.update(302, name="Deleted membership").status_code, 404)
        self.assertEqual(before, self.snapshot())

    def test_unavailable_ministry_and_admin_role_denied(self):
        for field, value in (("status", MinistryStatus.INACTIVE), ("deleted_at", datetime.now(timezone.utc))):
            previous = getattr(self.db.get(Ministry, 12), field)
            self.change(Ministry, 12, **{field: value})
            self.assertEqual(self.update(302, name="Unavailable").status_code, 404)
            self.change(Ministry, 12, **{field: previous})
        for model, identifier, field, value in (
            (MinistryMemberRole, 501, "revoked_at", datetime.now(timezone.utc)),
            (Role, 17, "status", RoleStatus.INACTIVE),
            (Role, 17, "deleted_at", datetime.now(timezone.utc)),
        ):
            previous = getattr(self.db.get(model, identifier), field)
            self.change(model, identifier, **{field: value})
            self.assertEqual(self.update(name="Unavailable").status_code, 403)
            self.change(model, identifier, **{field: previous})

    def test_add_participants_preserves_existing_attendance(self):
        before = self.snapshot()[1]
        response = self.update(ministry_member_ids=[203, 206, 205])
        self.assertEqual(response.status_code, 200, response.text)
        participants = response.json()["participants"]
        self.assertEqual({p["ministry_member_id"] for p in participants}, {203, 206, 205})
        for row in before:
            self.assertIn(row, self.snapshot()[1])
        added = self.db.scalar(select(RehearsalAttendance).where(
            RehearsalAttendance.rehearsal_id == 301, RehearsalAttendance.ministry_member_id == 205,
        ))
        self.assertIsNotNone(added)
        self.assertTrue(0 < added.id <= 9223372036854775807)
        self.assertIsNone(added.arrival_at)
        self.assertIsNone(added.attendance_status)

    def test_replace_participants_removes_only_unrecorded_rows(self):
        recorded = dict(self.db.execute(select(RehearsalAttendance.__table__).where(RehearsalAttendance.id == 401)).mappings().one())
        response = self.update(name="Replaced", ministry_member_ids=[203, 205])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({p["ministry_member_id"] for p in response.json()["participants"]}, {203, 205})
        self.assertIsNone(self.db.get(RehearsalAttendance, 402))
        current = dict(self.db.execute(select(RehearsalAttendance.__table__).where(RehearsalAttendance.id == 401)).mappings().one())
        self.assertEqual(current, recorded)
        self.assertIsNotNone(self.db.get(RehearsalAttendance, 404))

    def test_remove_unrecorded_participant(self):
        response = self.update(ministry_member_ids=[203])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p["ministry_member_id"] for p in response.json()["participants"]], [203])
        self.assertIsNone(self.db.get(RehearsalAttendance, 402))

    def test_recorded_removal_returns_conflict_and_rolls_back_fields(self):
        before = self.snapshot()
        response = self.update(name="Must rollback", ministry_member_ids=[206, 205])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("recorded", response.json()["detail"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.update(ministry_member_ids=[]).status_code, 409)
        self.assertEqual(before, self.snapshot())

    def test_arrival_or_status_alone_prevents_removal(self):
        for arrival, status in ((datetime.now(timezone.utc), None), (None, AttendanceStatus.ABSENT)):
            self.change(RehearsalAttendance, 402, arrival_at=arrival, attendance_status=status)
            before = self.snapshot()
            self.assertEqual(self.update(ministry_member_ids=[203]).status_code, 409)
            self.assertEqual(before, self.snapshot())

    def test_empty_list_permitted_when_all_rows_unrecorded(self):
        response = self.update(303, ministry_member_ids=[])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["participants"], [])
        self.assertIsNone(self.db.get(RehearsalAttendance, 404))
        self.assertIsNotNone(self.db.get(RehearsalAttendance, 401))

    def test_omitting_participants_preserves_inactive_historical_attendance(self):
        self.change(MinistryMember, 203, status=MembershipStatus.INACTIVE)
        before = self.snapshot()[1]
        self.assertEqual(self.update(description="Metadata only").status_code, 200)
        self.assertEqual(before, self.snapshot()[1])
        # An explicit desired list still validates every retained membership.
        self.assertEqual(self.update(ministry_member_ids=[203, 206]).status_code, 422)

    def test_invalid_participants_rollback_all_changes(self):
        before = self.snapshot()
        for desired in ([203, 99999], [203, 204], [203, 207]):
            self.assertEqual(self.update(name="Must rollback", ministry_member_ids=desired).status_code, 422)
            self.assertEqual(before, self.snapshot())
        for model, identifier, field, value in (
            (MinistryMember, 205, "status", MembershipStatus.PENDING),
            (MinistryMember, 205, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 205, "deleted_at", datetime.now(timezone.utc)),
            (User, 103, "status", UserStatus.INACTIVE),
            (User, 103, "deleted_at", datetime.now(timezone.utc)),
        ):
            previous = getattr(self.db.get(model, identifier), field)
            self.change(model, identifier, **{field: value})
            self.assertEqual(self.update(name="Must rollback", ministry_member_ids=[203, 205]).status_code, 422)
            self.assertEqual(before, self.snapshot())
            self.change(model, identifier, **{field: previous})

    def test_noop_preserves_updated_timestamp_and_attendance(self):
        before = self.snapshot()
        self.assertEqual(self.update(ministry_member_ids=[206, 203], name="Rehearsal 301").status_code, 200)
        self.assertEqual(before, self.snapshot())

    def test_participant_only_modification_updates_timestamp(self):
        before = self.db.get(Rehearsal, 301).updated_at
        self.assertEqual(self.update(ministry_member_ids=[203, 206, 205]).status_code, 200)
        self.db.expire_all()
        self.assertGreater(self.db.get(Rehearsal, 301).updated_at, before)

    def test_partial_insert_failure_rolls_back_deletes_metadata_and_attendance(self):
        before = self.snapshot()
        original = RehearsalRepository.add_attendance

        def fail_after_insert(repository, rehearsal_id, member_ids):
            original(repository, rehearsal_id, member_ids)
            raise SQLAlchemyError("private insert failure")

        with patch.object(RehearsalRepository, "add_attendance", autospec=True, side_effect=fail_after_insert):
            response = self.update(name="Must rollback", ministry_member_ids=[203, 205])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to update rehearsal."})
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.update(name="Retry", ministry_member_ids=[203, 205]).status_code, 200)

    def test_attendance_id_collision_rolls_back(self):
        before = self.snapshot()
        with patch("app.repositories.rehearsal.secrets.randbelow", return_value=400):
            self.assertEqual(self.update(name="Must rollback", ministry_member_ids=[203, 205]).status_code, 500)
        self.assertEqual(before, self.snapshot())

    def test_response_read_or_commit_failure_rolls_back(self):
        before = self.snapshot()
        for target, method in ((RehearsalReadRepository, "get_participants"), (type(self.db), "commit")):
            with patch.object(target, method, side_effect=SQLAlchemyError("private failure")):
                self.assertEqual(self.update(name="Must rollback", ministry_member_ids=[203, 205]).status_code, 500)
            self.assertEqual(before, self.snapshot())

    def test_constant_query_count_for_participant_replacement(self):
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            response = self.update(ministry_member_ids=[203, 205, 201])
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 200, response.text)
        # Auth, role, locked rehearsal, participant validation, locked attendance,
        # response metadata and participant functions: seven SELECTs, regardless of count.
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 7)

    def test_patch_cors_preflight(self):
        response = self.client.options("/rehearsals/301", headers={
            "Origin": settings.FRONTEND_ORIGIN, "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("PATCH", response.headers["access-control-allow-methods"])
