from pydantic import BaseModel, Field


class MinistryMemberInfo(BaseModel):
    ministry_member_id: int
    user_id: int
    first_name: str
    last_name: str
    functions: list[str] = Field(default_factory=list)


class MinistryMembersResponse(BaseModel):
    ministry_id: int
    members: list[MinistryMemberInfo] = Field(default_factory=list)
