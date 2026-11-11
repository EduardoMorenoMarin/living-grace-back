from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import AttendanceStatus, UserStatus
from app.schemas.rehearsal_read import RehearsalMinistry


class DashboardMembership(BaseModel):
    ministry_member_id: int
    ministry_id: int
    ministry_name: str
    functions: list[str]


class DashboardProfile(BaseModel):
    user_id: int
    display_name: str
    account_status: UserStatus
    is_global_admin: bool
    memberships: list[DashboardMembership]


class MonthlyAttendance(BaseModel):
    month: str
    timezone: str
    total_completed_assigned_rehearsals: int
    on_time: int
    late: int
    absent: int
    justified: int
    unresolved: int
    attendance_rate: float | None
    punctuality_rate: float | None


class DashboardRehearsal(BaseModel):
    rehearsal_id: int
    ministry: RehearsalMinistry
    name: str
    description: str | None
    start_at: datetime
    end_at: datetime
    tolerance_minutes: int


class RecentAttendance(BaseModel):
    rehearsal_id: int
    ministry: RehearsalMinistry
    rehearsal_date: date
    start_at: datetime
    end_at: datetime
    attendance_status: AttendanceStatus
    arrival_at: datetime | None


class DashboardResponse(BaseModel):
    selected_ministry_id: int | None
    profile: DashboardProfile
    monthly_attendance: MonthlyAttendance
    recent_attendance: list[RecentAttendance]
    next_rehearsal: DashboardRehearsal | None
    upcoming_rehearsals: list[DashboardRehearsal]
