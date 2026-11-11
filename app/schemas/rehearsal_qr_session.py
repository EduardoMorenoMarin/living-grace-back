from datetime import timezone

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator


class RehearsalQrSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    opens_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("opens_at", "expires_at")
    @classmethod
    def normalize_utc(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_window(self):
        if self.expires_at <= self.opens_at:
            raise ValueError("expires_at must be after opens_at.")
        return self


class RehearsalQrSessionResponse(BaseModel):
    id: int
    rehearsal_id: int
    check_in_url: str
    opens_at: AwareDatetime
    expires_at: AwareDatetime


class RehearsalQrSessionMetadata(BaseModel):
    id: int
    rehearsal_id: int
    opens_at: AwareDatetime
    expires_at: AwareDatetime
    created_at: AwareDatetime

    @field_validator("opens_at", "expires_at", "created_at")
    @classmethod
    def normalize_utc(cls, value):
        return value.astimezone(timezone.utc)
