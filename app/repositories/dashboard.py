from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models import Function, Ministry, MinistryMember, MinistryMemberFunction
from app.models.enums import AttendanceStatus, FunctionStatus, MembershipStatus, MinistryStatus
from app.repositories.rehearsal_read import RehearsalReadRepository


class DashboardRepository:
    def __init__(self, db: Session):
        self.db = db
        self.rehearsals = RehearsalReadRepository(db)

    def memberships(self, user_id: int):
        return self.db.execute(
            select(MinistryMember.id.label("ministry_member_id"),
                   Ministry.id.label("ministry_id"), Ministry.name.label("ministry_name"),
                   Function.name.label("function"))
            .select_from(MinistryMember)
            .join(Ministry, Ministry.id == MinistryMember.ministry_id)
            .outerjoin(MinistryMemberFunction, and_(
                MinistryMemberFunction.ministry_member_id == MinistryMember.id,
                MinistryMemberFunction.revoked_at.is_(None),
            ))
            .outerjoin(Function, and_(
                Function.id == MinistryMemberFunction.function_id,
                Function.ministry_id == Ministry.id,
                Function.status == FunctionStatus.ACTIVE, Function.deleted_at.is_(None),
            ))
            .where(MinistryMember.user_id == user_id,
                   MinistryMember.status == MembershipStatus.ACTIVE, MinistryMember.deleted_at.is_(None),
                   Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None))
            .order_by(Ministry.id, Function.id)
        ).mappings().all()

    def _assigned(self, user_id: int, ministry_id: int | None):
        # Always use personal participant visibility, even for global admins.
        statement = self.rehearsals._member_metadata(user_id).where(
            self.rehearsals._visibility(user_id, False),
        )
        assigned = statement.subquery()
        if ministry_id is not None:
            assigned = select(assigned).where(assigned.c.ministry_id == ministry_id).subquery()
        return assigned

    def monthly_counts(self, user_id, ministry_id, month_start, month_end, now):
        assigned = self._assigned(user_id, ministry_id)
        counts = [func.count().label("total")]
        counts.extend(func.count(case((assigned.c.attendance_status == status, 1))).label(status.value.lower())
                      for status in AttendanceStatus)
        counts.append(func.count(case((assigned.c.attendance_status.is_(None), 1))).label("unresolved"))
        return self.db.execute(select(*counts).select_from(assigned).where(
            assigned.c.start_at >= month_start, assigned.c.start_at < month_end,
            assigned.c.end_at <= now,
        )).mappings().one()

    def recent(self, user_id, ministry_id, now):
        assigned = self._assigned(user_id, ministry_id)
        return self.db.execute(select(assigned).where(
            assigned.c.end_at <= now, assigned.c.attendance_status.is_not(None),
        ).order_by(assigned.c.start_at.desc(), assigned.c.id.desc()).limit(5)).mappings().all()

    def upcoming(self, user_id, ministry_id, now):
        assigned = self._assigned(user_id, ministry_id)
        return self.db.execute(select(assigned).where(
            assigned.c.start_at >= now,
        ).order_by(assigned.c.start_at, assigned.c.id).limit(5)).mappings().all()
