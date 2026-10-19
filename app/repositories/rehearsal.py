from collections.abc import Iterator
from contextlib import contextmanager
import secrets

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RehearsalPersistenceError
from app.models.rehearsal import Rehearsal
from app.models.rehearsal_attendance import RehearsalAttendance
from app.models.ministry_member import MinistryMember
from app.models.user import User
from app.models.enums import MembershipStatus, UserStatus


class RehearsalRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, rehearsal: Rehearsal) -> None:
        self.db.add(rehearsal)
        self.db.flush()
        self.db.refresh(rehearsal)

    def eligible_participant_ids(self, ministry_id: int, member_ids: list[int]) -> set[int]:
        return set(self.db.scalars(
            select(MinistryMember.id)
            .join(User, User.id == MinistryMember.user_id)
            .where(
                MinistryMember.id.in_(member_ids), MinistryMember.ministry_id == ministry_id,
                MinistryMember.status == MembershipStatus.ACTIVE,
                MinistryMember.deleted_at.is_(None),
                User.status == UserStatus.ACTIVE, User.deleted_at.is_(None),
            ).with_for_update(read=True)
        ))

    def add_attendance(self, rehearsal_id: int, member_ids: list[int]) -> None:
        self.db.add_all([
            RehearsalAttendance(
                # Same positive BIGINT strategy used for role assignments.
                id=secrets.randbelow(9223372036854775807) + 1,
                rehearsal_id=rehearsal_id, ministry_member_id=member_id,
                arrival_at=None, attendance_status=None,
            )
            for member_id in member_ids
        ])
        self.db.flush()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise RehearsalPersistenceError("Unable to create rehearsal.") from None
        except Exception:
            self.db.rollback()
            raise
