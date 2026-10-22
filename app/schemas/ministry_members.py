from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MembershipStatus


class MinistryMemberInfo(BaseModel):
    ministry_member_id: int
    user_id: int
    first_name: str
    last_name: str
    status: MembershipStatus
    functions: list[str] = Field(default_factory=list)


class MinistryMembersResponse(BaseModel):
    ministry_id: int
    members: list[MinistryMemberInfo] = Field(default_factory=list)


class MembershipStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[MembershipStatus.ACTIVE]


class MembershipStatusResponse(BaseModel):
    ministry_id: int
    ministry_member_id: int
    status: MembershipStatus
