from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from sqlalchemy.exc import SQLAlchemyError

from app.core.notification_sockets import NotificationConnectionManager
from app.dependencies.auth import get_authenticated_user, get_current_user
from app.repositories.notification import NotificationRepository
from app.schemas.notification import NotificationItem
from app.schemas.notification_realtime import NotificationCreatedEvent, NotificationEventData


class NotificationSocketAuthenticator:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def authenticate(self, access_token: str) -> int:
        # Short-lived session, never a DB connection held for the socket lifetime.
        with self.session_factory() as db:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token)
            return get_current_user(get_authenticated_user(credentials, db)).id


class NotificationDeliveryService:
    """Future producers call publish_created(id) AFTER commit; no domain hooks yet.

    An independent READ COMMITTED session reads persisted data and owner identity.
    Uncommitted inserts are invisible; offline delivery does not remove DB rows.
    This is best effort, without a durable broker/outbox or automatic retries.
    """
    def __init__(self, session_factory, manager: NotificationConnectionManager):
        self.session_factory = session_factory
        self.manager = manager

    def _load_committed(self, notification_id):
        with self.session_factory() as db:
            notification = NotificationRepository(db).get_for_delivery(notification_id)
            if notification is None:
                return None
            item = NotificationItem.model_validate(notification)
            event = NotificationCreatedEvent(data=NotificationEventData(
                notification_type=item.type, **item.model_dump(exclude={"type"}),
            ))
            return notification.user_id, event.model_dump(mode="json")

    async def publish_created(self, notification_id: int) -> int:
        try:
            committed = await run_in_threadpool(self._load_committed, notification_id)
        except SQLAlchemyError:
            # Delivery is independent of the producer's already committed transaction.
            return 0
        if committed is None:
            return 0
        user_id, event = committed
        return await self.manager.send_to_user(user_id, event)
