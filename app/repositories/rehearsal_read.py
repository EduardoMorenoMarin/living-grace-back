from collections.abc import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_function import MinistryMemberFunction
from app.models.rehearsal import Rehearsal
from app.models.rehearsal_attendance import RehearsalAttendance
from app.models.user import User


class RehearsalReadRepository:
    """Apply visibility in SQL before pagination or detail retrieval."""

    def __init__(self, db: Session):
        self.db = db

    def _visibility(self, user_id: int, is_admin: bool):
        membership = select(MinistryMember.id).where(
            MinistryMember.user_id == user_id,
            MinistryMember.ministry_id == Rehearsal.ministry_id,
            MinistryMember.status == MembershipStatus.ACTIVE,
            MinistryMember.deleted_at.is_(None),
        )
        if not is_admin:
            membership = membership.join(
                RehearsalAttendance,
                RehearsalAttendance.ministry_member_id == MinistryMember.id,
            ).where(RehearsalAttendance.rehearsal_id == Rehearsal.id)
        return and_(
            Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None),
            membership.exists(),
        )

    def _metadata(self):
        return select(
            Rehearsal.id, Rehearsal.name, Rehearsal.description,
            Rehearsal.start_at, Rehearsal.end_at, Rehearsal.tolerance_minutes,
            Rehearsal.created_at, Rehearsal.updated_at,
            Ministry.id.label("ministry_id"), Ministry.name.label("ministry_name"),
            User.id.label("creator_id"), User.first_name.label("creator_first_name"),
            User.last_name.label("creator_last_name"),
        ).join(Ministry, Ministry.id == Rehearsal.ministry_id).join(User, User.id == Rehearsal.created_by)

    def list_visible(
        self, user_id: int, is_admin: bool, limit: int, offset: int, ministry_id: int | None,
    ) -> tuple[Sequence[RowMapping], int]:
        visible = self._visibility(user_id, is_admin)
        if ministry_id is not None:
            visible = and_(visible, Rehearsal.ministry_id == ministry_id)
        total = self.db.scalar(
            select(func.count()).select_from(Rehearsal)
            .join(Ministry, Ministry.id == Rehearsal.ministry_id).where(visible)
        )
        statement = self._metadata().where(visible)
        if is_admin:
            count = select(func.count(RehearsalAttendance.id)).where(
                RehearsalAttendance.rehearsal_id == Rehearsal.id,
            ).correlate(Rehearsal).scalar_subquery()
            statement = statement.add_columns(count.label("participant_count"))
        rows = self.db.execute(statement.order_by(Rehearsal.start_at, Rehearsal.id).limit(limit).offset(offset))
        return rows.mappings().all(), total or 0

    def get_visible(self, user_id: int, is_admin: bool, rehearsal_id: int) -> RowMapping | None:
        return self.db.execute(self._metadata().where(
            Rehearsal.id == rehearsal_id, self._visibility(user_id, is_admin),
        )).mappings().one_or_none()

    def get_admin_for_update(self, user_id: int, rehearsal_id: int) -> Rehearsal | None:
        return self.db.scalar(
            select(Rehearsal).join(Ministry, Ministry.id == Rehearsal.ministry_id)
            .where(Rehearsal.id == rehearsal_id, self._visibility(user_id, True))
            .with_for_update(of=Rehearsal)
            .execution_options(populate_existing=True)
        )

    def get_participants(self, rehearsal_id: int) -> Sequence[RowMapping]:
        # Retain selected participants and recorded attendance even if their
        # membership subsequently becomes inactive. Only ADMIN calls this read.
        return self.db.execute(
            select(
                MinistryMember.id.label("ministry_member_id"), User.id.label("user_id"),
                User.first_name, User.last_name, RehearsalAttendance.arrival_at,
                RehearsalAttendance.attendance_status, Function.name.label("function"),
            )
            .select_from(RehearsalAttendance)
            .join(MinistryMember, MinistryMember.id == RehearsalAttendance.ministry_member_id)
            .join(User, User.id == MinistryMember.user_id)
            .outerjoin(MinistryMemberFunction, and_(
                MinistryMemberFunction.ministry_member_id == MinistryMember.id,
                MinistryMemberFunction.revoked_at.is_(None),
            ))
            .outerjoin(Function, and_(
                Function.id == MinistryMemberFunction.function_id,
                Function.ministry_id == MinistryMember.ministry_id,
                Function.status == FunctionStatus.ACTIVE, Function.deleted_at.is_(None),
            ))
            .where(RehearsalAttendance.rehearsal_id == rehearsal_id)
            .order_by(MinistryMember.id, Function.id)
        ).mappings().all()
