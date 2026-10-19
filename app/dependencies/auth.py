from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies.database import get_db
from app.models.user import User


bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=401, detail="Invalid or missing access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(
            credentials.credentials, settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM], options={"require": ["sub", "exp"]},
        )
        user_id = int(claims["sub"])
        if not 0 < user_id <= 9223372036854775807:
            raise ValueError
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized from None
    try:
        user = db.get(User, user_id)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Unable to authenticate user.") from None
    if user is None:
        raise unauthorized
    return user
