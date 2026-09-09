from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse
from app.services.password_hasher import PasswordHasher
from app.services.registration_validator import RegistrationValidator
from app.services.user_factory import UserFactory


class RegistrationService:
    """Orchestrate registration using collaborators with separate responsibilities."""

    def __init__(
        self,
        repository: UserRepository,
        validator: RegistrationValidator,
        password_hasher: PasswordHasher,
        user_factory: UserFactory,
    ):
        self.repository = repository
        self.validator = validator
        self.password_hasher = password_hasher
        self.user_factory = user_factory

    def register(self, data: UserCreate) -> UserResponse:
        with self.repository.transaction():
            self.validator.validate(data)
            password_hash = self.password_hasher.hash(data.password.get_secret_value())
            user = self.user_factory.create(data, password_hash)
            self.repository.add(user)
            # Build the safe response before commit expires ORM attributes.
            result = UserResponse.model_validate(user)
        return result
