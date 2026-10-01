from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints, field_validator

from app.models.enums import UserStatus
from app.schemas.membership import MembershipCreate, MembershipResponse


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: EmailStr
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    password: SecretStr = Field(min_length=8, max_length=128)
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
    username: str
    first_name: str
    last_name: str
    phone: str | None
    birth_date: date | None
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    memberships: list[MembershipResponse] = Field(default_factory=list)
