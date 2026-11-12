import hashlib
from datetime import datetime, timezone

from app.core.attendance import arrival_status
from app.core.exceptions import AttendanceCheckInForbidden, AttendanceSessionInvalid, RehearsalAttendanceConflict
from app.models.enums import AttendanceStatus
from app.repositories.attendance_check_in import AttendanceCheckInRepository
from app.schemas.attendance_check_in import AttendanceCheckIn, AttendanceCheckInResponse


def utc_now():
    return datetime.now(timezone.utc)


class AttendanceCheckInService:
    def __init__(self, repository: AttendanceCheckInRepository):
        self.repository = repository

    def _validate_window(self, qr, rehearsal, now):
        if (qr is None or qr.revoked_at is not None
                or not (qr.opens_at <= now < qr.expires_at) or now >= rehearsal.end_at):
            raise AttendanceSessionInvalid

    def check_in(self, user_id: int, data: AttendanceCheckIn):
        token_hash = hashlib.sha256(data.token.get_secret_value().encode("utf-8")).hexdigest()
        with self.repository.transaction():
            rehearsal, qr = self.repository.get_session(token_hash)
            if rehearsal is None:
                raise AttendanceSessionInvalid
            self._validate_window(qr, rehearsal, utc_now())
            member = self.repository.get_eligible_member(rehearsal.ministry_id, user_id)
            if member is None:
                raise AttendanceCheckInForbidden
            attendance = self.repository.get_attendance(rehearsal.id, member.id)
            if attendance is not None and (
                attendance.arrival_at is not None or
                attendance.attendance_status not in (None, AttendanceStatus.ABSENT)
            ):
                raise RehearsalAttendanceConflict("Attendance already registered.")
            # Sample after every potentially blocking lock. NULL attendance rows
            # are the placeholders produced by existing rehearsal creation.
            arrival = utc_now()
            self._validate_window(qr, rehearsal, arrival)
            status = arrival_status(rehearsal.start_at, rehearsal.tolerance_minutes, arrival)
            self.repository.record(rehearsal.id, member.id, attendance, arrival, status)
            # A slow write must not cross expiration or completion. QR/rehearsal and
            # eligibility locks remain held until commit or rollback.
            self._validate_window(qr, rehearsal, utc_now())
            result = AttendanceCheckInResponse(
                rehearsal_id=rehearsal.id, attendance_status=status, arrival_at=arrival,
            )
        return result, attendance is None
