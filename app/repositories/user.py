from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def email_exists(self, email: str) -> bool:
        return bool(self.db.scalar(select(select(User.id).where(User.email == email).exists())))

    def username_exists(self, username: str) -> bool:
        return bool(self.db.scalar(select(select(User.id).where(User.username == username).exists())))

    def add(self, user: User) -> None:
        self.db.add(user)
        # Fetch database-generated identity/defaults without committing the flow.
        self.db.flush()
