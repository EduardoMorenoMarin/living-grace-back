from pwdlib.exceptions import UnknownHashError

from app.core.exceptions import (
    CurrentPasswordIncorrect, PasswordChangePersistenceError, PasswordChangeValidationError,
)
from app.repositories.user import UserRepository
from app.schemas.auth import ChangePasswordRequest
from app.services.password_hasher import PasswordHasher


class ChangePasswordService:
    def __init__(self, repository: UserRepository, password_hasher: PasswordHasher):
        self.repository = repository
        self.password_hasher = password_hasher

    def change(self, user_id: int, data: ChangePasswordRequest) -> None:
        with self.repository.password_change_transaction():
            user = self.repository.get_for_password_change(user_id)
            if user is None:
                raise PasswordChangePersistenceError
            current = data.current_password.get_secret_value()
            new = data.new_password.get_secret_value()
            try:
                matches = self.password_hasher.verify(current, user.password_hash)
            except UnknownHashError:
                raise PasswordChangePersistenceError from None
            if not matches:
                raise CurrentPasswordIncorrect
            if new == current:
                raise PasswordChangeValidationError
            self.repository.set_password(user, self.password_hasher.hash(new))
