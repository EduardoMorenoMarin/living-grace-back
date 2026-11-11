import secrets

from app.core.exceptions import PasswordResetUserIneligible, PasswordResetUserNotFound
from app.models.enums import UserStatus
from app.repositories.user import UserRepository
from app.schemas.user import PasswordResetResponse
from app.services.password_hasher import PasswordHasher


class ResetPasswordService:
    def __init__(self, repository: UserRepository, password_hasher: PasswordHasher):
        self.repository = repository
        self.password_hasher = password_hasher

    def reset(self, user_id: int) -> PasswordResetResponse:
        with self.repository.password_change_transaction():
            user = self.repository.get_for_password_change(user_id)
            if user is None or user.deleted_at is not None:
                raise PasswordResetUserNotFound
            if user.status != UserStatus.ACTIVE:
                raise PasswordResetUserIneligible
            temporary_password = secrets.token_urlsafe(24)
            self.repository.set_password(
                user, self.password_hasher.hash(temporary_password), must_change_password=True,
            )
            result = PasswordResetResponse(temporary_password=temporary_password)
        # Release the plaintext to the caller only after the commit succeeds.
        return result
