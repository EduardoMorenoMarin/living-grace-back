from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.dependencies.database import get_db
from app.models.user import User
from app.services.token import TokenService
from app.repositories.account_eligibility import eligible_account_conditions


bearer = HTTPBearer(auto_error=False)


def get_authenticated_user(
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
            algorithms=[settings.JWT_ALGORITHM], options={"require": ["sub", "exp", "pwdv"]},
        )
        user_id = int(claims["sub"])
        if not 0 < user_id <= 9223372036854775807:
            raise ValueError
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized from None
    try:
        user = db.execute(select(User).where(
            User.id == user_id, *eligible_account_conditions(),
        ).execution_options(populate_existing=True)).scalar_one_or_none()
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Unable to authenticate user.") from None
    if user is None or not TokenService(settings).matches_credentials(
        claims["pwdv"], user.id, user.password_hash,
    ):
        raise unauthorized
    return user


def get_current_user(user: Annotated[User, Depends(get_authenticated_user)]) -> User:
    """Business endpoints require completion of any mandatory password change."""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required.")
    return user
