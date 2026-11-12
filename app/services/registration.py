from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse
from app.services.password_hasher import PasswordHasher
from app.services.registration_validator import RegistrationValidator
from app.services.user_mapper import UserMapper
from app.services.membership_registration import MembershipRegistrationService
from app.services.membership_validator import MembershipValidator
from app.repositories.notification import NotificationRepository
from app.services.notification import NotificationService


class RegistrationService:
    """Orchestrate registration using collaborators with separate responsibilities."""

    def __init__(
        self,
        repository: UserRepository,
        validator: RegistrationValidator,
        password_hasher: PasswordHasher,
        user_mapper: UserMapper,
        membership_validator: MembershipValidator,
        membership_registration: MembershipRegistrationService,
        notifications: NotificationService | None = None,
    ):
        self.repository = repository
        self.validator = validator
        self.password_hasher = password_hasher
        self.user_mapper = user_mapper
        self.membership_validator = membership_validator
        self.membership_registration = membership_registration
        self.notifications = notifications if notifications is not None else NotificationService(
            NotificationRepository(repository.db),
        )

    def register(self, data: UserCreate) -> UserResponse:
        with self.repository.transaction():
            self.validator.validate(data)
            self.membership_validator.validate(data.memberships)
            password_hash = self.password_hasher.hash(data.password.get_secret_value())
            user = self.user_mapper.to_user(data, password_hash)
            self.repository.add(user)
            # Build the safe response before commit expires ORM attributes.
            result = UserResponse.model_validate(user)
            result.memberships = self.membership_registration.register(user.id, data.memberships)
            notification_ids = self.notifications.create_membership_pending([member.id for member in result.memberships])
        self.notifications.publish_after_commit(notification_ids)
        return result
