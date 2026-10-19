from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.authorization import get_authorization_repository
from app.dependencies.database import get_db
from app.models.user import User
from app.repositories.authorization import AuthorizationRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.rehearsal_read import RehearsalReadService, RehearsalViewer


def get_rehearsal_viewer(
    user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[AuthorizationRepository, Depends(get_authorization_repository)],
) -> RehearsalViewer:
    try:
        roles = repository.get_global_roles(user.id)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Unable to authorize user.") from None
    if not roles.intersection({"ADMIN", "USER"}):
        raise HTTPException(status_code=403, detail="Global ADMIN or USER role required.")
    return RehearsalViewer(user_id=user.id, is_admin="ADMIN" in roles)


def get_rehearsal_read_service(db: Annotated[Session, Depends(get_db)]) -> RehearsalReadService:
    return RehearsalReadService(RehearsalReadRepository(db))
