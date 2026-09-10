from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.password_hasher import PasswordHasher
from app.services.token import TokenService


class InvalidCredentials(Exception):
    pass


class LoginService:
    """Coordinate the existing credential-based login use case."""

    def __init__(
        self,
        repository: UserRepository,
        password_hasher: PasswordHasher,
        token_service: TokenService,
    ):
        self.repository = repository
        self.password_hasher = password_hasher
        self.token_service = token_service

    def login(self, data: LoginRequest) -> TokenResponse:
        user = self.repository.get_by_identifier(data.identifier)
        if user is None or not self.password_hasher.verify(
            data.password.get_secret_value(), user.password_hash
        ):
            raise InvalidCredentials("Invalid credentials.")

        return TokenResponse(access_token=self.token_service.create_access_token(user.id))
