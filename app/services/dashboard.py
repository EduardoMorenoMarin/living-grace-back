from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import DashboardReadError, MinistryNotFound
from app.models.user import User
from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import (
    DashboardMembership, DashboardProfile, DashboardRehearsal, DashboardResponse,
    MonthlyAttendance, RecentAttendance, AdminDashboardResponse, MinistryAttendance, RecentRehearsal,
)
from app.services.rehearsal_read import RehearsalViewer


def utc_now():
    return datetime.now(timezone.utc)


class DashboardService:
    def __init__(self, repository: DashboardRepository, application_timezone: str):
        self.repository = repository
        self.timezone = ZoneInfo(application_timezone)

    @staticmethod
    def _aware(value):
        # Treat timezone-less storage values as UTC; preserve explicit offsets.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _rehearsal(self, row):
        return dict(rehearsal_id=row["id"],
                    ministry={"id": row["ministry_id"], "name": row["ministry_name"]},
                    start_at=self._aware(row["start_at"]), end_at=self._aware(row["end_at"]))

    def get(self, user: User, viewer: RehearsalViewer, ministry_id: int | None) -> DashboardResponse | AdminDashboardResponse:
        now = utc_now()
        local_now = now.astimezone(self.timezone)
        month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        try:
            memberships = {}
            for row in self.repository.memberships(user.id):
                member = memberships.setdefault(row["ministry_id"], DashboardMembership(
                    **{key: row[key] for key in ("ministry_member_id", "ministry_id", "ministry_name")},
                    functions=[],
                ))
                if row["function"] is not None and row["function"] not in member.functions:
                    member.functions.append(row["function"])
            if ministry_id is not None and ministry_id not in memberships:
                raise MinistryNotFound("Ministry does not exist or is unavailable.")
            aggregate = self.repository.ministry_monthly_counts if viewer.is_admin else self.repository.monthly_counts
            counts = aggregate(
                user.id, ministry_id, month_start.astimezone(timezone.utc), month_end.astimezone(timezone.utc), now,
            )
            recent_query = self.repository.recent_rehearsals if viewer.is_admin else self.repository.recent
            recent = recent_query(user.id, ministry_id, now)
            upcoming = self.repository.upcoming(user.id, ministry_id, now, is_admin=viewer.is_admin)
        except SQLAlchemyError:
            raise DashboardReadError from None
        attended = counts["on_time"] + counts["late"]
        eligible = attended + counts["absent"]
        cards = [DashboardRehearsal(**self._rehearsal(row), name=row["name"],
                                    description=row["description"], tolerance_minutes=row["tolerance_minutes"])
                 for row in upcoming]
        shared = dict(
            selected_ministry_id=ministry_id,
            profile=DashboardProfile(user_id=user.id, display_name=f"{user.first_name} {user.last_name}".strip(),
                                     account_status=user.status, is_global_admin=viewer.is_admin,
                                     memberships=list(memberships.values())),
            next_rehearsal=cards[0] if cards else None, upcoming_rehearsals=cards,
        )
        if viewer.is_admin:
            return AdminDashboardResponse(
                **shared,
                ministry_attendance=MinistryAttendance(
                    month=month_start.strftime("%Y-%m"), timezone=self.timezone.key, **counts,
                    attendance_rate=round(attended / eligible * 100, 2) if eligible else None,
                    punctuality_rate=round(counts["on_time"] / attended * 100, 2) if attended else None,
                ),
                recent_rehearsals=[RecentRehearsal(
                    rehearsal_id=row["id"], name=row["name"],
                    ministry={"id": row["ministry_id"], "name": row["ministry_name"]},
                    rehearsal_date=self._aware(row["start_at"]).astimezone(self.timezone).date(),
                    total_participants=row["total_attendance_records"],
                    **{key: row[key] for key in ("on_time", "late", "absent", "justified", "unresolved")},
                    attendance_rate=self._attendance_rate(row),
                ) for row in recent],
            )
        return DashboardResponse(
            **shared,
            monthly_attendance=MonthlyAttendance(
                month=month_start.strftime("%Y-%m"), timezone=self.timezone.key,
                total_completed_assigned_rehearsals=counts["total"],
                **{key: counts[key] for key in ("on_time", "late", "absent", "justified", "unresolved")},
                attendance_rate=round(attended / eligible * 100, 2) if eligible else None,
                punctuality_rate=round(counts["on_time"] / attended * 100, 2) if attended else None,
            ),
            recent_attendance=[RecentAttendance(
                **self._rehearsal(row), rehearsal_date=self._aware(row["start_at"]).astimezone(self.timezone).date(),
                attendance_status=row["attendance_status"],
                arrival_at=self._aware(row["arrival_at"]) if row["arrival_at"] is not None else None,
            ) for row in recent],
        )

    @staticmethod
    def _attendance_rate(row):
        attended = row["on_time"] + row["late"]
        eligible = attended + row["absent"]
        return round(attended / eligible * 100, 2) if eligible else None
