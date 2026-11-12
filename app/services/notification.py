from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import NotificationReadError
from app.models.notification import Notification
from app.models.rehearsal import Rehearsal
from app.repositories.notification import NotificationRepository
from app.schemas.notification import (
    NotificationItem, NotificationPage, NotificationReadAllResponse, NotificationReadResponse,
    NotificationUnreadCount,
)


class NotificationService:
    def __init__(self, repository: NotificationRepository, publisher: Callable[[int], None] | None = None):
        self.repository = repository
        self.publisher = publisher

    def create_rehearsal_assignments(self, rehearsal: Rehearsal, new_member_ids: list[int]) -> list[int]:
        if not new_member_ids:
            return []
        user_ids = self.repository.assignment_user_ids(rehearsal.id, rehearsal.ministry_id, new_member_ids)
        return self.repository.add_many([
            Notification(user_id=user_id, type="REHEARSAL_ASSIGNED", title="New rehearsal assignment",
                         message=f'You have been assigned to rehearsal "{rehearsal.name}".',
                         resource_type="rehearsal", resource_id=rehearsal.id, is_read=False)
            for user_id in user_ids
        ])

    def publish_after_commit(self, notification_ids: list[int]) -> None:
        if self.publisher is None:
            return
        for notification_id in notification_ids:
            try:
                self.publisher(notification_id)
            except Exception:
                # Persistence has already committed. Delivery is best effort and
                # must neither fail the assignment response nor skip later users.
                continue

    def mark_read(self, user_id: int, notification_id: int) -> NotificationReadResponse:
        return NotificationReadResponse(**self.repository.mark_read(user_id, notification_id))

    def mark_all_read(self, user_id: int) -> NotificationReadAllResponse:
        return NotificationReadAllResponse(updated_count=self.repository.mark_all_read(user_id))

    def unread_count(self, user_id: int) -> NotificationUnreadCount:
        try:
            count = self.repository.unread_count(user_id)
        except SQLAlchemyError:
            raise NotificationReadError from None
        return NotificationUnreadCount(unread_count=count)

    def list_for_user(self, user_id: int, limit: int, offset: int, is_read: bool | None) -> NotificationPage:
        try:
            items, total = self.repository.list_for_user(user_id, limit, offset, is_read)
        except SQLAlchemyError:
            raise NotificationReadError from None
        return NotificationPage(total=total, limit=limit, offset=offset,
                                items=[NotificationItem.model_validate(item) for item in items])
