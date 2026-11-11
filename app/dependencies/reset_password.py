from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.user import UserRepository
from app.services.password_hasher import PasswordHasher
from app.services.reset_password import ResetPasswordService


def get_reset_password_service(db: Annotated[Session, Depends(get_db)]) -> ResetPasswordService:
    return ResetPasswordService(UserRepository(db), PasswordHasher())
