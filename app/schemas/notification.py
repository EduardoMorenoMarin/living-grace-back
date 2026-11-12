from datetime import datetime, timezone

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    message: str
    resource_type: str | None
    resource_id: int | None
    is_read: bool
    created_at: AwareDatetime

    @field_validator("created_at", mode="before")
    @classmethod
    def utc_created_at(cls, value):
        # SQLite test storage loses tzinfo; PostgreSQL TIMESTAMPTZ preserves it.
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class NotificationPage(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    items: list[NotificationItem]


class NotificationUnreadCount(BaseModel):
    unread_count: int = Field(ge=0)
