from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.user import UserRepository
from app.services.change_password import ChangePasswordService
from app.services.password_hasher import PasswordHasher


def get_change_password_service(db: Annotated[Session, Depends(get_db)]) -> ChangePasswordService:
    return ChangePasswordService(UserRepository(db), PasswordHasher())
