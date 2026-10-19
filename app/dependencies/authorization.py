from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.database import get_db
from app.models.user import User
from app.repositories.authorization import AuthorizationRepository


def get_authorization_repository(
    db: Annotated[Session, Depends(get_db)],
) -> AuthorizationRepository:
    return AuthorizationRepository(db)


def require_global_admin(
    user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[AuthorizationRepository, Depends(get_authorization_repository)],
) -> User:
    """Authorize current database privileges and return the authenticated identity."""
    try:
        allowed = repository.has_global_role(user.id, "ADMIN")
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Unable to authorize user.") from None
    if not allowed:
        raise HTTPException(status_code=403, detail="Global ADMIN role required.")
    return user
