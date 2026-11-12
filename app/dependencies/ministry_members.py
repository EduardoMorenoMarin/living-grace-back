from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.ministry_members import MinistryMembersRepository
from app.services.ministry_members import MinistryMembersService
from app.dependencies.notification import get_membership_notification_service
from app.services.notification import NotificationService


def get_ministry_members_service(
    db: Annotated[Session, Depends(get_db)],
    notifications: Annotated[NotificationService, Depends(get_membership_notification_service)],
) -> MinistryMembersService:
    return MinistryMembersService(MinistryMembersRepository(db), notifications)
