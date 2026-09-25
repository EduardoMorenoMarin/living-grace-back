from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.user import UserRepository
from app.services.password_hasher import PasswordHasher
from app.services.registration import RegistrationService
from app.services.registration_validator import RegistrationValidator
from app.services.user_mapper import UserMapper
from app.repositories.membership import MembershipRepository
from app.services.membership_registration import MembershipRegistrationService
from app.services.membership_validator import MembershipValidator


def get_registration_service(db: Annotated[Session, Depends(get_db)]) -> RegistrationService:
    repository = UserRepository(db)
    memberships = MembershipRepository(db)
    return RegistrationService(
        repository=repository,
        validator=RegistrationValidator(repository),
        password_hasher=PasswordHasher(),
        user_mapper=UserMapper(),
        membership_validator=MembershipValidator(memberships),
        membership_registration=MembershipRegistrationService(memberships),
    )
