from app.models.user import User
from app.schemas.user import UserCreate


class UserFactory:
    """Map registration fields into a user, leaving database defaults intact."""

    def create(self, data: UserCreate, password_hash: str) -> User:
        return User(
            email=str(data.email),
            username=data.username,
            password_hash=password_hash,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            birth_date=data.birth_date,
        )
