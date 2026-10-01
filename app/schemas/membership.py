from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import MembershipStatus


DatabaseId = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ministry_id: DatabaseId
    function_ids: list[DatabaseId] = Field(default_factory=list)

    @field_validator("function_ids")
    @classmethod
    def unique_assignments(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("Assignments must not contain duplicate IDs.")
        return values


class MembershipResponse(BaseModel):
    id: int
    ministry_id: int
    status: MembershipStatus
    function_ids: list[int]
