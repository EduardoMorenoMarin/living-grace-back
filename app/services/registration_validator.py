from app.core.exceptions import RegistrationConflict
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


class RegistrationValidator:
    """Enforce registration uniqueness rules, in the existing order."""

    def __init__(self, repository: UserRepository):
        self.repository = repository

    def validate(self, data: UserCreate) -> None:
        if self.repository.email_exists(str(data.email)):
            raise RegistrationConflict("Email is already registered.")
        if self.repository.username_exists(data.username):
            raise RegistrationConflict("Username is already registered.")
