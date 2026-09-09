from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.user import UserRepository
from app.services.password_hasher import PasswordHasher
from app.services.registration import RegistrationService
from app.services.registration_validator import RegistrationValidator
from app.services.user_factory import UserFactory


def get_registration_service(db: Annotated[Session, Depends(get_db)]) -> RegistrationService:
    repository = UserRepository(db)
    return RegistrationService(
        repository=repository,
        validator=RegistrationValidator(repository),
        password_hasher=PasswordHasher(),
        user_factory=UserFactory(),
    )
