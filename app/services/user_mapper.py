from app.models.user import User
from app.schemas.user import UserCreate
from app.models.enums import UserStatus


class UserMapper:
    """New registrations await approval of their first active membership."""

    def to_user(self, data: UserCreate, password_hash: str) -> User:
        return User(
            status=UserStatus.PENDING,
            email=str(data.email),
            password_hash=password_hash,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            birth_date=data.birth_date,
        )
