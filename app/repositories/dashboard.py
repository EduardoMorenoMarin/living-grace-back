from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.attendance import REPORTABLE_STATUSES, effective_status
from app.models import Function, Ministry, MinistryMember, MinistryMemberFunction, Rehearsal, RehearsalAttendance
from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus
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

    def _assigned(self, user_id: int, ministry_id: int | None, now):
        # Attendance statistics always use personal participant visibility.
        statement = self.rehearsals._member_metadata(user_id, now).where(
            self.rehearsals._visibility(user_id, False),
        )
        assigned = statement.subquery()
        if ministry_id is not None:
            assigned = select(assigned).where(assigned.c.ministry_id == ministry_id).subquery()
        return assigned

    def monthly_counts(self, user_id, ministry_id, month_start, month_end, now):
        assigned = self._assigned(user_id, ministry_id, now)
        counts = [func.count().label("total")]
        counts.extend(func.count(case((assigned.c.attendance_status == status, 1))).label(status.value.lower())
                      for status in REPORTABLE_STATUSES)
        return self.db.execute(select(*counts).select_from(assigned).where(
            assigned.c.start_at >= month_start, assigned.c.start_at < month_end,
            assigned.c.end_at <= now,
        )).mappings().one()

    def recent(self, user_id, ministry_id, now):
        assigned = self._assigned(user_id, ministry_id, now)
        return self.db.execute(select(assigned).where(
            assigned.c.end_at <= now, assigned.c.attendance_status.is_not(None),
        ).order_by(assigned.c.start_at.desc(), assigned.c.id.desc()).limit(5)).mappings().all()

    def upcoming(self, user_id, ministry_id, now, *, is_admin: bool):
        statement = (self.rehearsals._metadata() if is_admin
                     else self.rehearsals._member_metadata(user_id, now)).where(
            self.rehearsals._visibility(user_id, is_admin),
        )
        visible = statement.subquery()
        statement = select(visible).where(visible.c.start_at >= now)
        if ministry_id is not None:
            statement = statement.where(visible.c.ministry_id == ministry_id)
        return self.db.execute(statement.order_by(
            visible.c.start_at.asc(), visible.c.id.asc(),
        ).limit(5)).mappings().all()

    def _admin_rehearsals(self, user_id, ministry_id):
        statement = self.rehearsals._metadata().where(self.rehearsals._visibility(user_id, True))
        if ministry_id is not None:
            statement = statement.where(Rehearsal.ministry_id == ministry_id)
        return statement.subquery()

    def _attendance_totals(self, visible, now):
        # Preserve historical assignments, including members who later became inactive.
        # Only real assignment records contribute to totals or status counts.
        records = select(RehearsalAttendance.id, RehearsalAttendance.rehearsal_id,
                         effective_status(now)).join(
            MinistryMember, MinistryMember.id == RehearsalAttendance.ministry_member_id,
        ).join(Rehearsal, Rehearsal.id == RehearsalAttendance.rehearsal_id).where(
            MinistryMember.ministry_id == Rehearsal.ministry_id,
        ).subquery()
        counts = [func.count(records.c.id).label("total_attendance_records")]
        counts.extend(func.count(case((records.c.attendance_status == status, 1))).label(status.value.lower())
                      for status in REPORTABLE_STATUSES)
        return select(visible, *counts).outerjoin(records, and_(
            records.c.rehearsal_id == visible.c.id, visible.c.end_at <= now,
        )).group_by(*visible.c).subquery()

    def ministry_monthly_counts(self, user_id, ministry_id, month_start, month_end, now):
        visible = self._admin_rehearsals(user_id, ministry_id)
        monthly = select(visible).where(
            visible.c.start_at >= month_start, visible.c.start_at < month_end,
        ).subquery()
        totals = self._attendance_totals(monthly, now)
        # Count current active people once, even across multiple visible ministries.
        ministries = select(MinistryMember.ministry_id).where(
            MinistryMember.user_id == user_id, MinistryMember.status == MembershipStatus.ACTIVE,
            MinistryMember.deleted_at.is_(None), Ministry.status == MinistryStatus.ACTIVE,
            Ministry.deleted_at.is_(None),
        ).join(Ministry, Ministry.id == MinistryMember.ministry_id)
        if ministry_id is not None:
            ministries = ministries.where(Ministry.id == ministry_id)
        members = select(func.count(func.distinct(MinistryMember.user_id))).where(
            MinistryMember.ministry_id.in_(ministries),
            MinistryMember.status == MembershipStatus.ACTIVE, MinistryMember.deleted_at.is_(None),
        ).scalar_subquery()
        return self.db.execute(select(
            func.count().label("total_rehearsals"),
            func.count(case((totals.c.end_at <= now, 1))).label("completed_rehearsals"),
            members.label("total_members"),
            *[func.coalesce(func.sum(totals.c[key]), 0).label(key) for key in
              ("total_attendance_records", "on_time", "late", "absent")],
        ).select_from(totals)).mappings().one()

    def recent_rehearsals(self, user_id, ministry_id, now):
        visible = self._admin_rehearsals(user_id, ministry_id)
        recent = select(visible).where(visible.c.end_at <= now).order_by(
            visible.c.start_at.desc(), visible.c.id.desc(),
        ).limit(5).subquery()
        totals = self._attendance_totals(recent, now)
        return self.db.execute(select(totals).order_by(
            totals.c.start_at.desc(), totals.c.id.desc(),
        )).mappings().all()
