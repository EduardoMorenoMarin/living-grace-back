"""Effective attendance for reads; never mutate historical assignment records."""
from datetime import timedelta

from sqlalchemy import DateTime, case, cast, null
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from app.models.enums import AttendanceStatus
from app.models.rehearsal import Rehearsal
from app.models.rehearsal_attendance import RehearsalAttendance


REPORTABLE_STATUSES = (AttendanceStatus.ON_TIME, AttendanceStatus.LATE, AttendanceStatus.ABSENT)


class ToleranceCutoff(FunctionElement):
    type = DateTime(timezone=True)
    inherit_cache = True


@compiles(ToleranceCutoff, "postgresql")
def _postgres_cutoff(element, compiler, **kw):
    start, minutes = [compiler.process(arg, **kw) for arg in element.clauses]
    return f"({start} + {minutes} * INTERVAL '1 minute')"


@compiles(ToleranceCutoff, "sqlite")
def _sqlite_cutoff(element, compiler, **kw):
    start, minutes = [compiler.process(arg, **kw) for arg in element.clauses]
    # SQLite datetime() drops fractions. Retain SQLAlchemy's stored microseconds
    # so exact-cutoff comparisons agree with PostgreSQL and Python.
    return (f"(strftime('%Y-%m-%d %H:%M:%S', substr({start}, 1, 19), printf('%+d minutes', {minutes}))"
            f" || substr({start}, 20))")


def arrival_status(start_at, tolerance_minutes, arrival_at):
    """Classify a recorded arrival; callers validate the check-in window."""
    cutoff = start_at + timedelta(minutes=tolerance_minutes)
    return AttendanceStatus.ON_TIME if arrival_at <= cutoff else AttendanceStatus.LATE


def effective_status(now):
    """SQL status for one real assignment, preserving explicit legacy values.

    NULL + arrival derives punctuality, including legacy arrivals after end_at.
    NULL without arrival becomes ABSENT only when end_at <= now. Guard the row
    ID so an outer join cannot turn an unassigned member into an absence.
    """
    attendance = RehearsalAttendance
    status_type = attendance.__table__.c.attendance_status.type
    return case(
        (attendance.id.is_(None), cast(null(), status_type)),
        (attendance.attendance_status.is_not(None), attendance.attendance_status),
        (attendance.arrival_at.is_not(None), case(
            (attendance.arrival_at <= ToleranceCutoff(Rehearsal.start_at, Rehearsal.tolerance_minutes),
             cast(AttendanceStatus.ON_TIME.value, status_type)),
            else_=cast(AttendanceStatus.LATE.value, status_type),
        )),
        (Rehearsal.end_at <= now, cast(AttendanceStatus.ABSENT.value, status_type)),
        else_=cast(null(), status_type),
    ).label("attendance_status")


def attendance_rates(counts):
    """Percentages over pooled counts; legacy JUSTIFIED/pending are excluded."""
    attended = counts["on_time"] + counts["late"]
    eligible = attended + counts["absent"]
    return dict(
        attendance_rate=round(attended / eligible * 100, 2) if eligible else None,
        punctuality_rate=round(counts["on_time"] / attended * 100, 2) if attended else None,
    )
