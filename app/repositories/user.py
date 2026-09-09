from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RegistrationConflict, RegistrationPersistenceError
from app.models.user import User


class UserRepository:
    """Own user database access and transaction handling."""

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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit a completed operation and translate persistence failures safely."""
        try:
            yield
            self.db.commit()
        except RegistrationConflict:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            # A concurrent request can insert after the uniqueness checks.
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise RegistrationConflict("Email or username is already registered.") from None
            raise RegistrationPersistenceError("Unable to register user.") from None
        except SQLAlchemyError:
            self.db.rollback()
            raise RegistrationPersistenceError("Unable to register user.") from None
