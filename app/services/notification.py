from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import NotificationReadError
from app.repositories.notification import NotificationRepository
from app.schemas.notification import NotificationItem, NotificationPage


class NotificationService:
    def __init__(self, repository: NotificationRepository):
        self.repository = repository

    def list_for_user(self, user_id: int, limit: int, offset: int, is_read: bool | None) -> NotificationPage:
        try:
            items, total = self.repository.list_for_user(user_id, limit, offset, is_read)
        except SQLAlchemyError:
            raise NotificationReadError from None
        return NotificationPage(total=total, limit=limit, offset=offset,
                                items=[NotificationItem.model_validate(item) for item in items])
