from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models.enums import UserStatus
from app.schemas.membership import MembershipCreate, MembershipResponse
from app.schemas.password import NewPassword


class PasswordResetResponse(BaseModel):
    temporary_password: str = Field(repr=False)
    must_change_password: Literal[True] = True


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: EmailStr
    password: NewPassword
    first_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    last_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    phone: str | None = None
    birth_date: date | None = None
    memberships: list[MembershipCreate] = Field(default_factory=list)

    @field_validator("memberships")
    @classmethod
    def unique_ministries(cls, values: list[MembershipCreate]) -> list[MembershipCreate]:
        ministry_ids = [membership.ministry_id for membership in values]
        if len(ministry_ids) != len(set(ministry_ids)):
            raise ValueError("Each ministry may only be selected once.")
        return values


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str
    last_name: str
    phone: str | None
    birth_date: date | None
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    memberships: list[MembershipResponse] = Field(default_factory=list)
