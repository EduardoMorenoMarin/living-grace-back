from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AttendanceStatus


class RehearsalMinistry(BaseModel):
    id: int
    name: str


class RehearsalCreatorInfo(BaseModel):
    id: int
    first_name: str
    last_name: str


class RehearsalInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    ministry: RehearsalMinistry
    name: str
    description: str | None
    start_at: datetime
    end_at: datetime
    tolerance_minutes: int
    created_at: datetime
    updated_at: datetime
    created_by: RehearsalCreatorInfo


class MyRehearsalAttendance(BaseModel):
    attendance_status: AttendanceStatus | None = Field(
        description="Effective status; null is pending. JUSTIFIED is read-only historical compatibility."
    )
    arrival_at: datetime | None


class MemberRehearsalItem(RehearsalInfo, MyRehearsalAttendance):
    pass


class MemberRehearsalDetail(RehearsalInfo):
    my_attendance: MyRehearsalAttendance


class AdminRehearsalItem(RehearsalInfo):
    participant_count: int


class RehearsalParticipant(BaseModel):
    ministry_member_id: int
    user_id: int
    first_name: str
    last_name: str
    functions: list[str] = Field(default_factory=list)
    arrival_at: datetime | None
    attendance_status: AttendanceStatus | None = Field(
        description="Effective status; null is pending. JUSTIFIED is read-only historical compatibility."
    )


class AdminRehearsalDetail(RehearsalInfo):
    participants: list[RehearsalParticipant]


class RehearsalPage(BaseModel):
    items: list[MemberRehearsalItem]
    total: int


class AdminRehearsalPage(BaseModel):
    items: list[AdminRehearsalItem]
    total: int
