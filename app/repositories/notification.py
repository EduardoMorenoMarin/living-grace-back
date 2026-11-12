from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.notification import Notification


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_user(self, user_id: int, limit: int, offset: int, is_read: bool | None):
        conditions = [Notification.user_id == user_id]
        if is_read is not None:
            conditions.append(Notification.is_read == is_read)
        total = self.db.scalar(select(func.count()).select_from(Notification).where(*conditions))
        items = self.db.scalars(select(Notification).where(*conditions).order_by(
            Notification.created_at.desc(), Notification.id.desc(),
        ).limit(limit).offset(offset)).all()
        return items, total or 0
