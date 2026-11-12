"""Dashboard contract with real SQL, role eligibility, JWTs and fixed UTC time."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_rehearsal_read as fixtures
from pydantic import ValidationError
from sqlalchemy import delete, event
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, settings
from app.models import (
    Function, Ministry, MinistryMember, MinistryMemberFunction, MinistryMemberRole,
    Rehearsal, RehearsalAttendance, Role, User,
)
from app.models.enums import AttendanceStatus, FunctionStatus, MembershipStatus, MinistryStatus, RoleScope, RoleStatus, UserStatus
from app.repositories.dashboard import DashboardRepository


UTC = timezone.utc


class DashboardTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.db.execute(delete(RehearsalAttendance))
        self.db.execute(delete(Rehearsal))
        self.db.commit()
        self.now = datetime(2026, 11, 15, 17, tzinfo=UTC)
        self.stack.enter_context(patch("app.services.dashboard.utc_now", return_value=self.now))
        self.stack.enter_context(patch.object(settings, "APP_TIMEZONE", "America/Lima"))
        self.sequence = 1000
        self.login(102)

    def add(self, start, status=None, member=203, ministry=11, arrival=None, end=None):
        self.sequence += 1
        rehearsal = Rehearsal(id=self.sequence, ministry_id=ministry, created_by=103,
                              name=f"Rehearsal {self.sequence}", description="Practice",
                              start_at=start, end_at=end or start + timedelta(hours=1), tolerance_minutes=10)
        self.db.add(rehearsal)
        self.db.flush()
        self.db.add(RehearsalAttendance(id=self.sequence, rehearsal_id=self.sequence,
                                       ministry_member_id=member, attendance_status=status, arrival_at=arrival))
        self.db.commit()
        return self.sequence

    def dashboard(self, **params):
        return self.client.get("/dashboard/me", headers=self.headers, params=params)

    def body(self, **params):
        response = self.dashboard(**params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def populate(self):
        ids = []
        for day, status, member, ministry in (
            (2, AttendanceStatus.ON_TIME, 203, 11), (3, AttendanceStatus.LATE, 203, 11),
            (4, AttendanceStatus.ABSENT, 204, 12), (5, AttendanceStatus.JUSTIFIED, 204, 12),
            (6, None, 203, 11), (7, None, 203, 11),
        ):
            start = datetime(2026, 11, day, 17, tzinfo=UTC)
            arrival = start if status in (AttendanceStatus.ON_TIME, AttendanceStatus.LATE) or day == 7 else None
            ids.append(self.add(start, status, member, ministry, arrival))
        upcoming = [self.add(self.now + timedelta(days=day)) for day in range(1, 8)]
        return ids, upcoming

    def test_member_profile_multiple_ministries_summary_and_personal_privacy(self):
        completed, upcoming = self.populate()
        other_arrival = datetime(2026, 11, 2, 18, 23, tzinfo=UTC)
        self.db.add(RehearsalAttendance(id=9999, rehearsal_id=completed[0], ministry_member_id=206,
                                       attendance_status=AttendanceStatus.LATE, arrival_at=other_arrival))
        self.db.commit()
        self.add(self.now - timedelta(days=1), AttendanceStatus.ON_TIME, member=207, ministry=13)
        body = self.body()
        self.assertNotIn("ministry_attendance", body)
        self.assertNotIn("recent_rehearsals", body)
        self.assertEqual(body["profile"], {
            "user_id": 102, "display_name": "Regular Last", "account_status": "ACTIVE", "is_global_admin": False,
            "memberships": [
                {"ministry_member_id": 203, "ministry_id": 11, "ministry_name": "Ministry 11", "functions": ["Teclado", "Vocalista"]},
                {"ministry_member_id": 204, "ministry_id": 12, "ministry_name": "Ministry 12", "functions": []},
            ],
        })
        self.assertEqual(body["monthly_attendance"], {
            "month": "2026-11", "timezone": "America/Lima", "total_completed_assigned_rehearsals": 6,
            "on_time": 1, "late": 1, "absent": 1, "justified": 1, "unresolved": 2,
            "attendance_rate": 66.67, "punctuality_rate": 50.0,
        })
        self.assertEqual([r["rehearsal_id"] for r in body["recent_attendance"]], completed[3::-1])
        self.assertEqual([r["rehearsal_id"] for r in body["upcoming_rehearsals"]], upcoming[:5])
        self.assertEqual(body["next_rehearsal"], body["upcoming_rehearsals"][0])
        for secret in ("password_hash", "private-hash", "participants", "OtherParticipant", "18:23"):
            self.assertNotIn(secret, str(body))

    def test_ministry_filter_applies_before_aggregation_and_limits(self):
        completed, _ = self.populate()
        selected_future = self.add(self.now + timedelta(days=20), member=204, ministry=12)
        body = self.body(ministry_id=12)
        self.assertEqual(body["selected_ministry_id"], 12)
        self.assertEqual(len(body["profile"]["memberships"]), 2)
        summary = body["monthly_attendance"]
        self.assertEqual(summary["total_completed_assigned_rehearsals"], 2)
        self.assertEqual(summary["attendance_rate"], 0.0)
        self.assertIsNone(summary["punctuality_rate"])
        self.assertEqual([r["rehearsal_id"] for r in body["recent_attendance"]], [completed[3], completed[2]])
        self.assertEqual([r["rehearsal_id"] for r in body["upcoming_rehearsals"]], [selected_future])
        for ministry in (13, 99999):
            self.assertEqual(self.dashboard(ministry_id=ministry).status_code, 404)
        for ministry in (0, -1, "invalid", 9223372036854775808):
            self.assertEqual(self.dashboard(ministry_id=ministry).status_code, 422)

    def test_admin_upcoming_and_ministry_attendance(self):
        _, upcoming = self.populate()
        own_past = self.add(self.now - timedelta(days=2), AttendanceStatus.LATE, member=201)
        own_future = self.add(self.now + timedelta(hours=1), member=202, ministry=12)
        self.login(101)
        body = self.body()
        self.assertTrue(body["profile"]["is_global_admin"])
        self.assertEqual(body["ministry_attendance"]["completed_rehearsals"], 7)
        self.assertEqual(body["recent_rehearsals"][0]["rehearsal_id"], own_past)
        self.assertEqual(len(body["recent_rehearsals"]), 5)
        self.assertNotIn("monthly_attendance", body)
        self.assertNotIn("recent_attendance", body)
        self.assertEqual([r["rehearsal_id"] for r in body["upcoming_rehearsals"]], [own_future] + upcoming[:4])
        self.assertEqual(body["next_rehearsal"], body["upcoming_rehearsals"][0])
        self.assertNotIn("participant_count", str(body))

    def test_upcoming_visibility_filter_order_limit_and_next_for_both_roles(self):
        # Insert out of chronological order, including equal start times.
        later = self.add(self.now + timedelta(days=8))
        unassigned = self.add(self.now + timedelta(hours=1), member=206)
        at_now = self.add(self.now)
        tied = self.add(self.now)
        selected = self.add(self.now + timedelta(days=2), member=204, ministry=12)
        extra = [self.add(self.now + timedelta(days=day)) for day in (5, 3, 4)]
        self.add(self.now - timedelta(seconds=1), member=206)
        self.add(self.now, member=207, ministry=13)
        # An admin also sees rehearsals with no participants at all.
        self.db.execute(delete(RehearsalAttendance).where(RehearsalAttendance.rehearsal_id == unassigned))
        self.db.commit()
        for user, expected in (
            (102, [at_now, tied, selected, extra[1], extra[2]]),
            (101, [at_now, tied, unassigned, selected, extra[1]]),
        ):
            with self.subTest(user=user):
                self.login(user)
                body = self.body(is_admin=True, role="ADMIN")
                self.assertEqual([r["rehearsal_id"] for r in body["upcoming_rehearsals"]], expected)
                self.assertEqual(body["next_rehearsal"], body["upcoming_rehearsals"][0])
                filtered = self.body(ministry_id=12)
                self.assertEqual([r["rehearsal_id"] for r in filtered["upcoming_rehearsals"]], [selected])
                self.assertEqual(filtered["next_rehearsal"], filtered["upcoming_rehearsals"][0])
                self.assertEqual(self.dashboard(ministry_id=13).status_code, 404)
                ministry11 = self.body(ministry_id=11)
                self.assertTrue(all(r["ministry"]["id"] == 11 for r in ministry11["upcoming_rehearsals"]))
                self.assertNotIn(later, [r["rehearsal_id"] for r in ministry11["upcoming_rehearsals"]])

    def test_admin_upcoming_requires_active_membership_and_ministry(self):
        self.add(self.now, member=204, ministry=12)
        self.add(self.now - timedelta(days=1), AttendanceStatus.ON_TIME, member=204, ministry=12)
        self.login(101)
        cases = (
            (MinistryMember, 202, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 202, "status", MembershipStatus.PENDING),
            (MinistryMember, 202, "deleted_at", self.now),
            (Ministry, 12, "status", MinistryStatus.INACTIVE),
            (Ministry, 12, "deleted_at", self.now),
        )
        for model, identifier, key, value in cases:
            with self.subTest(model=model, key=key, value=value):
                previous = getattr(self.db.get(model, identifier), key)
                self.change(model, identifier, **{key: value})
                body = self.body()
                self.assertEqual(body["upcoming_rehearsals"], [])
                self.assertIsNone(body["next_rehearsal"])
                self.assertEqual(body["ministry_attendance"]["total_rehearsals"], 0)
                self.assertEqual(body["ministry_attendance"]["total_attendance_records"], 0)
                self.assertEqual(body["recent_rehearsals"], [])
                self.assertEqual(self.dashboard(ministry_id=12).status_code, 404)
                self.change(model, identifier, **{key: previous})

    def test_admin_mixed_statuses_scoping_and_historical_participants(self):
        rehearsal = self.add(self.now - timedelta(days=1), AttendanceStatus.ON_TIME)
        for identifier, member, status in ((9001, 201, AttendanceStatus.LATE),
                                            (9002, 205, AttendanceStatus.ABSENT),
                                            (9003, 206, AttendanceStatus.JUSTIFIED)):
            self.db.add(RehearsalAttendance(id=identifier, rehearsal_id=rehearsal,
                                           ministry_member_id=member, attendance_status=status))
        unresolved = self.add(self.now - timedelta(days=2))
        future = self.add(self.now + timedelta(days=1), AttendanceStatus.ABSENT)
        self.add(self.now - timedelta(days=1), AttendanceStatus.ABSENT, member=204, ministry=12)
        self.add(self.now - timedelta(days=1), AttendanceStatus.ABSENT, member=207, ministry=13)
        self.db.commit()
        # Historical results remain even after a participant becomes inactive.
        self.change(MinistryMember, 206, status=MembershipStatus.INACTIVE)
        self.login(101)
        body = self.body(ministry_id=11)
        self.assertEqual(body["ministry_attendance"], {
            "month": "2026-11", "timezone": "America/Lima", "total_rehearsals": 3,
            "completed_rehearsals": 2, "total_members": 3, "total_attendance_records": 5,
            "on_time": 1, "late": 1, "absent": 1, "justified": 1, "unresolved": 1,
            "attendance_rate": 66.67, "punctuality_rate": 50.0,
        })
        self.assertEqual(body["recent_rehearsals"], [
            {"rehearsal_id": rehearsal, "ministry": {"id": 11, "name": "Ministry 11"},
             "name": f"Rehearsal {rehearsal}", "rehearsal_date": "2026-11-14",
             "total_participants": 4, "on_time": 1, "late": 1, "absent": 1,
             "justified": 1, "unresolved": 0, "attendance_rate": 66.67},
            {"rehearsal_id": unresolved, "ministry": {"id": 11, "name": "Ministry 11"},
             "name": f"Rehearsal {unresolved}", "rehearsal_date": "2026-11-13",
             "total_participants": 1, "on_time": 0, "late": 0, "absent": 0,
             "justified": 0, "unresolved": 1, "attendance_rate": None},
        ])
        self.assertEqual(body["next_rehearsal"]["rehearsal_id"], future)
        other = self.body(ministry_id=12)["ministry_attendance"]
        self.assertEqual(other["total_attendance_records"], 1)
        self.assertEqual(other["attendance_rate"], 0.0)
        self.assertIsNone(other["punctuality_rate"])
        all_visible = self.body()["ministry_attendance"]
        self.assertEqual(all_visible["total_attendance_records"], 6)
        self.assertEqual(all_visible["total_members"], 3)
        self.assertEqual(self.dashboard(ministry_id=13).status_code, 404)

    def test_admin_empty_and_unassigned_rehearsals(self):
        self.login(101)
        body = self.body(ministry_id=11)
        summary = body["ministry_attendance"]
        self.assertEqual(summary["total_members"], 4)
        for key in ("total_rehearsals", "completed_rehearsals", "total_attendance_records",
                    "on_time", "late", "absent", "justified", "unresolved"):
            self.assertEqual(summary[key], 0)
        self.assertIsNone(summary["attendance_rate"])
        self.assertIsNone(summary["punctuality_rate"])
        self.assertEqual(body["recent_rehearsals"], [])
        self.assertEqual(body["upcoming_rehearsals"], [])
        self.assertIsNone(body["next_rehearsal"])
        rehearsal = self.add(self.now - timedelta(days=1))
        self.db.execute(delete(RehearsalAttendance))
        self.db.commit()
        body = self.body(ministry_id=11)
        self.assertEqual(body["ministry_attendance"]["completed_rehearsals"], 1)
        self.assertEqual(body["ministry_attendance"]["total_attendance_records"], 0)
        self.assertEqual(body["recent_rehearsals"][0]["rehearsal_id"], rehearsal)
        self.assertEqual(body["recent_rehearsals"][0]["total_participants"], 0)
        self.assertEqual(body["recent_rehearsals"][0]["unresolved"], 0)

    def test_admin_month_boundaries_completed_cutoff_and_recent_order(self):
        outside = self.add(datetime(2026, 11, 1, 4, 59, tzinfo=UTC), AttendanceStatus.ABSENT)
        self.add(datetime(2026, 11, 1, 5, tzinfo=UTC), AttendanceStatus.JUSTIFIED)
        ongoing = self.add(self.now - timedelta(minutes=1), AttendanceStatus.ABSENT,
                           end=self.now + timedelta(hours=1))
        ended = self.add(self.now - timedelta(hours=1), AttendanceStatus.ON_TIME, end=self.now)
        tied = self.add(self.now - timedelta(hours=1), AttendanceStatus.LATE, end=self.now)
        future = self.add(datetime(2026, 12, 1, 5, tzinfo=UTC), AttendanceStatus.ABSENT)
        self.login(101)
        body = self.body(ministry_id=11)
        summary = body["ministry_attendance"]
        self.assertEqual(summary["total_rehearsals"], 4)
        self.assertEqual(summary["completed_rehearsals"], 3)
        self.assertEqual(summary["total_attendance_records"], 3)
        self.assertEqual(summary["absent"], 0)
        self.assertEqual(summary["attendance_rate"], 100.0)
        self.assertEqual(summary["punctuality_rate"], 50.0)
        recent = body["recent_rehearsals"]
        self.assertEqual([r["rehearsal_id"] for r in recent[:2]], [tied, ended])
        self.assertEqual(recent[-1]["rehearsal_id"], outside)
        self.assertEqual(recent[-1]["rehearsal_date"], "2026-10-31")
        self.assertNotIn(ongoing, [r["rehearsal_id"] for r in recent])
        self.assertEqual(body["next_rehearsal"]["rehearsal_id"], future)

    def test_admin_authorization_is_server_side_and_failures_are_sanitized(self):
        self.populate()
        body = self.body(role="ADMIN", is_admin=True)
        self.assertIn("monthly_attendance", body)
        self.assertNotIn("ministry_attendance", body)
        self.login(101)
        self.change(MinistryMemberRole, 501, revoked_at=self.now)
        body = self.body(role="ADMIN", is_admin=True)
        self.assertFalse(body["profile"]["is_global_admin"])
        self.assertIn("monthly_attendance", body)
        self.assertNotIn("ministry_attendance", body)
        self.change(MinistryMemberRole, 501, revoked_at=None)
        for method in ("ministry_monthly_counts", "recent_rehearsals"):
            with self.subTest(method=method), patch.object(DashboardRepository, method,
                                                         side_effect=SQLAlchemyError("private")):
                response = self.dashboard()
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Unable to retrieve dashboard."})

    def test_empty_dashboard_has_zero_counts_null_rates_and_no_next(self):
        body = self.body()
        summary = body["monthly_attendance"]
        for key in ("total_completed_assigned_rehearsals", "on_time", "late", "absent", "justified", "unresolved"):
            self.assertEqual(summary[key], 0)
        self.assertIsNone(summary["attendance_rate"])
        self.assertIsNone(summary["punctuality_rate"])
        self.assertEqual(body["recent_attendance"], [])
        self.assertEqual(body["upcoming_rehearsals"], [])
        self.assertIsNone(body["next_rehearsal"])

    def test_justified_and_null_records_never_become_absences(self):
        self.add(self.now - timedelta(days=2), AttendanceStatus.JUSTIFIED)
        self.add(self.now - timedelta(days=1))
        summary = self.body()["monthly_attendance"]
        self.assertEqual(summary["justified"], 1)
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(summary["absent"], 0)
        self.assertIsNone(summary["attendance_rate"])
        self.assertIsNone(summary["punctuality_rate"])

    def test_completed_and_upcoming_cutoffs_and_recent_order_are_deterministic(self):
        recent = [self.add(self.now - timedelta(days=day), AttendanceStatus.ON_TIME) for day in range(1, 9)]
        # Tied start times use IDs to establish a stable newest-first order.
        tie = self.add(self.now - timedelta(days=1), AttendanceStatus.ABSENT)
        self.add(self.now - timedelta(minutes=1), AttendanceStatus.ON_TIME, end=self.now + timedelta(hours=1))
        at_now = self.add(self.now)
        at_now2 = self.add(self.now)
        # Completed exactly now is included, even though the next event starts now.
        ended = self.add(self.now - timedelta(hours=1), AttendanceStatus.LATE, end=self.now)
        body = self.body()
        self.assertEqual([r["rehearsal_id"] for r in body["recent_attendance"]], [ended, tie] + recent[:3])
        self.assertEqual([r["rehearsal_id"] for r in body["upcoming_rehearsals"]], [at_now, at_now2])
        self.assertEqual(body["monthly_attendance"]["total_completed_assigned_rehearsals"], 10)

    def test_inactive_pending_deleted_memberships_and_ministries_are_excluded(self):
        self.add(self.now - timedelta(days=1), AttendanceStatus.ON_TIME, member=204, ministry=12)
        self.add(self.now + timedelta(days=1), member=204, ministry=12)
        cases = (
            (MinistryMember, 204, "status", MembershipStatus.INACTIVE),
            (MinistryMember, 204, "status", MembershipStatus.PENDING),
            (MinistryMember, 204, "deleted_at", self.now),
            (Ministry, 12, "status", MinistryStatus.INACTIVE),
            (Ministry, 12, "deleted_at", self.now),
        )
        for model, identifier, key, value in cases:
            with self.subTest(model=model, key=key, value=value):
                previous = getattr(self.db.get(model, identifier), key)
                self.change(model, identifier, **{key: value})
                body = self.body()
                self.assertEqual([m["ministry_id"] for m in body["profile"]["memberships"]], [11])
                self.assertEqual(body["monthly_attendance"]["total_completed_assigned_rehearsals"], 0)
                self.assertEqual(body["recent_attendance"], [])
                self.assertEqual(body["upcoming_rehearsals"], [])
                self.assertEqual(self.dashboard(ministry_id=12).status_code, 404)
                self.change(model, identifier, **{key: previous})

    def test_function_revocation_status_and_cross_ministry_are_respected(self):
        self.change(MinistryMemberFunction, 701, revoked_at=self.now)
        self.change(Function, 602, status=FunctionStatus.INACTIVE)
        self.assertEqual(self.body()["profile"]["memberships"][0]["functions"], [])

    def test_missing_invalid_and_stale_jwt(self):
        original = self.headers
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            self.headers = headers
            self.assertEqual(self.dashboard().status_code, 401)
        self.headers = original
        self.change(User, 102, password_hash="changed-hash")
        self.assertEqual(self.dashboard().status_code, 401)

    def test_forced_password_change_blocks_dashboard(self):
        self.change(User, 102, must_change_password=True)
        response = self.dashboard()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Password change required.")

    def test_role_and_user_eligibility_match_rehearsal_reads(self):
        cases = (
            (Role, 18, "scope", RoleScope.MINISTRY), (Role, 18, "status", RoleStatus.INACTIVE),
            (MinistryMemberRole, 503, "revoked_at", self.now),
            (MinistryMember, 203, "status", MembershipStatus.INACTIVE),
            (User, 102, "status", UserStatus.INACTIVE), (User, 102, "deleted_at", self.now),
        )
        for model, identifier, key, value in cases:
            with self.subTest(model=model, key=key):
                previous = getattr(self.db.get(model, identifier), key)
                self.change(model, identifier, **{key: value})
                self.assertEqual(self.dashboard().status_code, 403)
                self.change(model, identifier, **{key: previous})
        self.login(104)
        self.assertEqual(self.dashboard().status_code, 403)

    def test_month_boundaries_use_local_rehearsal_start_date(self):
        self.add(datetime(2026, 11, 1, 4, 59, tzinfo=UTC), AttendanceStatus.ABSENT)
        included = self.add(datetime(2026, 11, 1, 5, tzinfo=UTC), AttendanceStatus.ON_TIME)
        summary = self.body()["monthly_attendance"]
        self.assertEqual(summary["total_completed_assigned_rehearsals"], 1)
        self.assertEqual(summary["on_time"], 1)
        self.assertEqual(summary["attendance_rate"], 100)
        recent = self.body()["recent_attendance"]
        self.assertEqual(recent[0]["rehearsal_id"], included)
        self.assertEqual(recent[0]["rehearsal_date"], "2026-11-01")
        self.assertEqual(recent[1]["rehearsal_date"], "2026-10-31")
        with patch.object(settings, "APP_TIMEZONE", "UTC"):
            self.assertEqual(self.body()["monthly_attendance"]["total_completed_assigned_rehearsals"], 2)

    def test_local_current_month_year_rollover_and_dst_boundaries(self):
        # UTC January 1 is still December in Lima.
        self.add(datetime(2026, 12, 1, 5, tzinfo=UTC), AttendanceStatus.ON_TIME)
        self.add(datetime(2027, 1, 1, 4, tzinfo=UTC), AttendanceStatus.LATE,
                 end=datetime(2027, 1, 1, 4, 30, tzinfo=UTC))
        with patch("app.services.dashboard.utc_now", return_value=datetime(2027, 1, 1, 4, 30, tzinfo=UTC)):
            summary = self.body()["monthly_attendance"]
        self.assertEqual(summary["month"], "2026-12")
        self.assertEqual(summary["total_completed_assigned_rehearsals"], 2)
        # March starts at UTC-5 and ends at UTC-4 in New York.
        with patch.object(settings, "APP_TIMEZONE", "America/New_York"), \
                patch("app.services.dashboard.utc_now", return_value=datetime(2026, 3, 31, 23, tzinfo=UTC)), \
                patch.object(DashboardRepository, "monthly_counts", wraps=DashboardRepository(self.db).monthly_counts) as aggregate:
            self.body()
        args = aggregate.call_args.args
        self.assertEqual(args[2], datetime(2026, 3, 1, 5, tzinfo=UTC))
        self.assertEqual(args[3], datetime(2026, 4, 1, 4, tzinfo=UTC))

    def test_invalid_timezone_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(APP_TIMEZONE="not/a-timezone")

    def test_constant_query_count_and_no_writes(self):
        def capture():
            statements = []
            self.db.expire_all()
            def record(conn, cursor, statement, parameters, context, many):
                statements.append(statement)
            event.listen(self.engine, "before_cursor_execute", record)
            try:
                self.body()
            finally:
                event.remove(self.engine, "before_cursor_execute", record)
            self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
            return len(statements)
        self.assertEqual(capture(), 6)
        self.populate()
        self.assertEqual(capture(), 6)
        self.login(101)
        self.assertEqual(capture(), 6)

    def test_database_failures_are_sanitized(self):
        for method in ("memberships", "monthly_counts", "recent", "upcoming"):
            with self.subTest(method=method), patch.object(DashboardRepository, method, side_effect=SQLAlchemyError("private")):
                response = self.dashboard()
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Unable to retrieve dashboard."})
