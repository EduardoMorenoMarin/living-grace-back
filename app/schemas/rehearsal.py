from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.membership import DatabaseId


class RehearsalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ministry_id: DatabaseId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    description: str | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime
    tolerance_minutes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 0
    ministry_member_ids: list[DatabaseId] = Field(min_length=1)

    @field_validator("ministry_member_ids")
    @classmethod
    def unique_participants(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("Participants must not contain duplicate membership IDs.")
        return values

    @model_validator(mode="after")
    def validate_interval(self) -> "RehearsalCreate":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at.")
        return self


class RehearsalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ministry_id: int
    created_by: int
    name: str
    description: str | None
    start_at: datetime
    end_at: datetime
    tolerance_minutes: int
    created_at: datetime
    updated_at: datetime
