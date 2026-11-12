from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.dependencies.notification import get_rehearsal_notification_service
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.notification import NotificationService
from app.services.rehearsal_delete import RehearsalDeleteService


def get_rehearsal_delete_service(
    db: Annotated[Session, Depends(get_db)],
    notifications: Annotated[NotificationService, Depends(get_rehearsal_notification_service)],
) -> RehearsalDeleteService:
    return RehearsalDeleteService(RehearsalRepository(db), RehearsalReadRepository(db), notifications)
