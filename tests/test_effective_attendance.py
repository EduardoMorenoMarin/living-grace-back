"""One attendance interpretation across individual reads and SQL summaries."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_rehearsal_read as fixtures
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.core.attendance import arrival_status, effective_status
from app.models import Rehearsal, RehearsalAttendance
from app.models.enums import AttendanceStatus


class EffectiveAttendanceTests(unittest.TestCase):
    login = fixtures.RehearsalReadTests.login
    change = fixtures.RehearsalReadTests.change

    def setUp(self):
        fixtures.RehearsalReadTests.setUp(self)
        self.now = datetime(2026, 11, 15, 17, tzinfo=timezone.utc)
        self.start = self.now - timedelta(hours=2)
        self.change(Rehearsal, 301, start_at=self.start, end_at=self.now, tolerance_minutes=10)
        for target in ("app.repositories.rehearsal_read.utc_now", "app.services.rehearsal_read.utc_now",
                       "app.services.dashboard.utc_now"):
            self.stack.enter_context(patch(target, return_value=self.now))

    def get(self, path):
        response = self.client.get(path, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_consistent(self, expected, *, completed=True):
        self.login(102)
        detail = self.get("/rehearsals/301")["my_attendance"]
        page = self.get("/rehearsals?ministry_id=11")["items"][0]
        self.assertEqual(detail["attendance_status"], expected)
        self.assertEqual(page["attendance_status"], expected)
        personal = self.get("/dashboard/me?ministry_id=11")
        self.assertEqual(personal["monthly_attendance"]["total_completed_assigned_rehearsals"], int(completed))
        if completed:
            self.assertEqual(personal["recent_attendance"][0]["attendance_status"], expected)
        else:
            self.assertEqual(personal["recent_attendance"], [])
        self.login(101)
        participant = next(p for p in self.get("/rehearsals/301")["participants"]
                           if p["ministry_member_id"] == 203)
        self.assertEqual(participant["attendance_status"], expected)
        admin = self.get("/dashboard/me?ministry_id=11")
        # The rehearsal has two real participants, regardless of either status.
        self.assertEqual(admin["ministry_attendance"]["total_attendance_records"], 2 if completed else 0)
        if completed:
            recent = next(r for r in admin["recent_rehearsals"] if r["rehearsal_id"] == 301)
            self.assertEqual(recent["total_participants"], 2)
            for status in ("on_time", "late", "absent"):
                self.assertEqual(personal["monthly_attendance"][status], int(expected == status.upper()))
            self.assertEqual(recent["absent"], 1 + int(expected == "ABSENT"))
        for summary in (personal["monthly_attendance"], admin["ministry_attendance"]):
            self.assertNotIn("justified", summary)
            self.assertNotIn("unresolved", summary)
        return personal, admin

    def test_completed_missing_arrival_is_absent_and_gets_never_write(self):
        self.change(RehearsalAttendance, 401, attendance_status=None, arrival_at=None)
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            personal, admin = self.assert_consistent("ABSENT")
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
        self.assertEqual(personal["monthly_attendance"]["attendance_rate"], 0.0)
        self.assertEqual(admin["ministry_attendance"]["attendance_rate"], 0.0)
        self.db.expire_all()
        self.assertIsNone(self.db.get(RehearsalAttendance, 401).attendance_status)

    def test_pending_and_unassigned_members_are_not_absent(self):
        self.change(RehearsalAttendance, 401, attendance_status=None, arrival_at=None)
        self.change(Rehearsal, 301, end_at=self.now + timedelta(microseconds=1))
        self.assert_consistent(None, completed=False)
        self.login(101)
        page = self.get("/rehearsals?ministry_id=11")
        rehearsal = next(r for r in page["items"] if r["id"] == 301)
        self.assertEqual(rehearsal["participant_count"], 2)
        self.assertEqual(len(self.get("/rehearsals/301")["participants"]), 2)
        # Admin is an active ministry member but is not assigned to this rehearsal.
        self.assertNotIn(201, [p["ministry_member_id"] for p in self.get("/rehearsals/301")["participants"]])

    def test_null_status_with_arrival_uses_tolerance_in_all_responses(self):
        for delta, expected in ((timedelta(minutes=10), "ON_TIME"),
                                (timedelta(minutes=10, microseconds=1), "LATE")):
            with self.subTest(delta=delta):
                self.change(RehearsalAttendance, 401, attendance_status=None, arrival_at=self.start + delta)
                self.assert_consistent(expected)
                self.db.expire_all()
                self.assertIsNone(self.db.get(RehearsalAttendance, 401).attendance_status)

    def test_ongoing_arrival_is_reported_without_becoming_completed(self):
        self.change(Rehearsal, 301, end_at=self.now + timedelta(hours=1))
        self.change(RehearsalAttendance, 401, attendance_status=None,
                    arrival_at=self.start + timedelta(minutes=11))
        self.assert_consistent("LATE", completed=False)

    def test_historical_justified_is_read_only_compatibility_and_excluded_from_rates(self):
        self.change(RehearsalAttendance, 401, attendance_status=AttendanceStatus.JUSTIFIED, arrival_at=None)
        personal, admin = self.assert_consistent("JUSTIFIED")
        self.assertIsNone(personal["monthly_attendance"]["attendance_rate"])
        self.assertIsNone(personal["monthly_attendance"]["punctuality_rate"])
        self.assertEqual(admin["ministry_attendance"]["attendance_rate"], 0.0)
        self.db.expire_all()
        self.assertEqual(self.db.get(RehearsalAttendance, 401).attendance_status, AttendanceStatus.JUSTIFIED)

    def test_update_response_uses_same_effective_status(self):
        self.change(RehearsalAttendance, 401, attendance_status=None, arrival_at=None)
        response = self.client.patch("/rehearsals/301", headers=self.headers, json={"name": "Updated"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(p["attendance_status"] == "ABSENT" for p in response.json()["participants"]))
        self.db.expire_all()
        self.assertIsNone(self.db.get(RehearsalAttendance, 401).attendance_status)

    def test_sql_and_check_in_agree_at_fractional_cutoff(self):
        # Exercise the actual SQL expression at microsecond precision and day rollover.
        start = datetime(2026, 11, 14, 23, 55, 0, 999999, tzinfo=timezone.utc)
        self.change(Rehearsal, 301, start_at=start)
        for offset in (-1, 0, 1):
            arrival = start + timedelta(minutes=10, microseconds=offset)
            self.change(RehearsalAttendance, 401, attendance_status=None, arrival_at=arrival)
            status = self.db.scalar(select(effective_status(self.now)).select_from(RehearsalAttendance)
                                    .join(Rehearsal).where(RehearsalAttendance.id == 401))
            self.assertEqual(status, arrival_status(start, 10, arrival))
        sql = str(select(effective_status(self.now)).compile(dialect=postgresql.dialect()))
        self.assertIn("INTERVAL '1 minute'", sql)
        self.assertIn(" AS attendance_status)", sql)
