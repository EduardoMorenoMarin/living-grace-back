from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, SecretStr, field_serializer


class NotificationSocketTicket(BaseModel):
    ticket: SecretStr
    expires_in: int = Field(gt=0)

    @field_serializer("ticket", when_used="json")
    def serialize_ticket(self, value):
        return value.get_secret_value()


class NotificationEventData(BaseModel):
    id: int
    notification_type: str
    title: str
    message: str
    resource_type: str | None
    resource_id: int | None
    is_read: bool
    created_at: AwareDatetime


class NotificationCreatedEvent(BaseModel):
    type: Literal["notification.created"] = "notification.created"
    data: NotificationEventData
