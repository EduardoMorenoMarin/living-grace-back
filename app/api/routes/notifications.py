from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.exceptions import NotificationReadError
from app.dependencies.auth import get_current_user
from app.dependencies.notification import get_notification_service
from app.models.user import User
from app.schemas.notification import NotificationPage, NotificationUnreadCount
from app.services.notification import NotificationService


router = APIRouter(prefix="/notifications", tags=["notifications"])


# Keep static paths before future routes with notification ID parameters.
@router.get("/unread-count", response_model=NotificationUnreadCount)
def get_unread_count(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    response: Response,
) -> NotificationUnreadCount:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.unread_count(user.id)
    except NotificationReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve notifications.") from None


@router.get("", response_model=NotificationPage)
def list_notifications(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    is_read: bool | None = None,
) -> NotificationPage:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.list_for_user(user.id, limit, offset, is_read)
    except NotificationReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve notifications.") from None
