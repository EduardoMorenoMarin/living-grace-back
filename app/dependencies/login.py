from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies.database import get_db
from app.repositories.user import UserRepository
from app.services.login import LoginService
from app.services.password_hasher import PasswordHasher
from app.services.token import TokenService


def get_login_service(db: Annotated[Session, Depends(get_db)]) -> LoginService:
    return LoginService(
        repository=UserRepository(db),
        password_hasher=PasswordHasher(),
        token_service=TokenService(settings),
    )
