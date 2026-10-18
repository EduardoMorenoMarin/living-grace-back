from datetime import date, datetime

from pydantic import BaseModel, Field


class ProfileMembership(BaseModel):
    ministry: str
    functions: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str | None
    birth_date: date | None
    created_at: datetime
    memberships: list[ProfileMembership] = Field(default_factory=list)
