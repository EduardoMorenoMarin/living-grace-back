from typing import Annotated

from anyio import from_thread
from fastapi import BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.dependencies.notification_realtime import get_notification_delivery_service
from app.repositories.notification import NotificationRepository
from app.services.notification import NotificationService
from app.services.notification_realtime import NotificationDeliveryService


def get_notification_service(db: Annotated[Session, Depends(get_db)]) -> NotificationService:
    return NotificationService(NotificationRepository(db))


def get_membership_notification_service(
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
    delivery: Annotated[NotificationDeliveryService, Depends(get_notification_delivery_service)],
) -> NotificationService:
    async def deliver(notification_id: int):
        try:
            await delivery.publish_created(notification_id)
        except Exception:
            # Failure after the response must not stop subsequent recipients.
            pass

    def publish(notification_id: int):
        # Called only after the business commit. Async background tasks run after
        # the response, without holding a synchronous request worker token.
        background_tasks.add_task(deliver, notification_id)

    return NotificationService(NotificationRepository(db), publisher=publish)


def get_rehearsal_notification_service(
    db: Annotated[Session, Depends(get_db)],
    delivery: Annotated[NotificationDeliveryService, Depends(get_notification_delivery_service)],
) -> NotificationService:
    def publish(notification_id: int):
        # Rehearsal services run in FastAPI's AnyIO worker thread. Schedule socket
        # work on its parent application loop, never on a new asyncio.run() loop.
        from_thread.run(delivery.publish_created, notification_id)
    return NotificationService(NotificationRepository(db), publisher=publish)
