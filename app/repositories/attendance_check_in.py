from contextlib import contextmanager
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import AttendanceCheckInPersistenceError, RehearsalAttendanceConflict
from app.models import Ministry, MinistryMember, Rehearsal, RehearsalAttendance, RehearsalQrSession, User
from app.models.enums import MembershipStatus, MinistryStatus, UserStatus


class AttendanceCheckInRepository:
    def __init__(self, db: Session):
        self.db = db
        self._recording_pair = None

    def _locked(self, model, *conditions):
        return self.db.scalar(select(model).where(*conditions).with_for_update()
                              .execution_options(populate_existing=True))

    def get_session(self, token_hash: str):
        rehearsal_id = self.db.scalar(select(RehearsalQrSession.rehearsal_id).where(
            RehearsalQrSession.token_hash == token_hash,
        ))
        if rehearsal_id is None:
            return None, None
        # Match rehearsal administration's parent-first lock order. Re-read the
        # QR row after waiting so concurrent revocation cannot be missed.
        rehearsal = self._locked(Rehearsal, Rehearsal.id == rehearsal_id)
        qr = self._locked(RehearsalQrSession, RehearsalQrSession.token_hash == token_hash,
                          RehearsalQrSession.rehearsal_id == rehearsal_id)
        return rehearsal, qr

    def get_eligible_member(self, ministry_id: int, user_id: int):
        ministry = self._locked(Ministry, Ministry.id == ministry_id)
        user = self._locked(User, User.id == user_id)
        member = self._locked(MinistryMember, MinistryMember.user_id == user_id,
                              MinistryMember.ministry_id == ministry_id)
        if (ministry is None or ministry.status != MinistryStatus.ACTIVE or ministry.deleted_at is not None
                or user is None or user.status != UserStatus.ACTIVE or user.deleted_at is not None
                or member is None or member.status != MembershipStatus.ACTIVE or member.deleted_at is not None):
            return None
        return member

    def get_attendance(self, rehearsal_id: int, member_id: int):
        return self._locked(RehearsalAttendance, RehearsalAttendance.rehearsal_id == rehearsal_id,
                            RehearsalAttendance.ministry_member_id == member_id)

    def record(self, rehearsal_id, member_id, existing, arrival_at, status):
        self._recording_pair = (rehearsal_id, member_id)
        if existing is None:
            existing = RehearsalAttendance(
                id=secrets.randbelow(9223372036854775807) + 1,
                rehearsal_id=rehearsal_id, ministry_member_id=member_id,
            )
            self.db.add(existing)
        existing.arrival_at = arrival_at
        existing.attendance_status = status
        self.db.flush()

    @contextmanager
    def transaction(self):
        self._recording_pair = None
        try:
            yield
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            # Only the attendance pair constraint is a duplicate; a random PK
            # collision or other constraint failure must remain a sanitized 500.
            sqlite_pair = "rehearsal_attendance.rehearsal_id, rehearsal_attendance.ministry_member_id"
            duplicate = sqlite_pair in str(exc.orig)
            if getattr(exc.orig, "sqlstate", None) == "23505" and self._recording_pair:
                # The live constraint was created manually; avoid depending on
                # its name. Check the intended pair after the failed tx rolls back.
                try:
                    rehearsal_id, member_id = self._recording_pair
                    duplicate = self.db.scalar(select(RehearsalAttendance.id).where(
                        RehearsalAttendance.rehearsal_id == rehearsal_id,
                        RehearsalAttendance.ministry_member_id == member_id,
                    )) is not None
                except SQLAlchemyError:
                    raise AttendanceCheckInPersistenceError from None
                finally:
                    self.db.rollback()
            if duplicate:
                raise RehearsalAttendanceConflict("Attendance already registered.") from None
            raise AttendanceCheckInPersistenceError from None
        except SQLAlchemyError:
            self.db.rollback()
            raise AttendanceCheckInPersistenceError from None
        except Exception:
            self.db.rollback()
            raise
