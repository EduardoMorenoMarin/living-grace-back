from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import AttendanceStatus


class AttendanceCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: SecretStr = Field(min_length=1, max_length=512)


class AttendanceCheckInResponse(BaseModel):
    rehearsal_id: int
    attendance_status: AttendanceStatus
    arrival_at: datetime
