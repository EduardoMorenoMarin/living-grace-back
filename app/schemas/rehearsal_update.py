from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.membership import DatabaseId
from app.schemas.rehearsal import RehearsalCreate


class RehearsalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    description: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    tolerance_minutes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] | None = None
    ministry_member_ids: list[DatabaseId] | None = None

    @field_validator("ministry_member_ids")
    @classmethod
    def unique_participants(cls, values: list[int] | None) -> list[int] | None:
        if values is not None:
            return RehearsalCreate.unique_participants(values)
        return values

    @model_validator(mode="after")
    def validate_patch(self) -> "RehearsalUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one editable field must be provided.")
        for field in self.model_fields_set - {"description"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null.")
        return self
