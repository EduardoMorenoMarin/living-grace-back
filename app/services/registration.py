from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse


class RegistrationConflict(Exception):
    pass


class RegistrationPersistenceError(Exception):
    pass


class RegistrationService:
    """BEFORE-SRP: uniqueness, hashing, construction, and persistence share one service."""

    def __init__(self, db: Session):
        self.db = db
        self.repository = UserRepository(db)

    def register(self, data: UserCreate) -> UserResponse:
        try:
            if self.repository.email_exists(str(data.email)):
                raise RegistrationConflict("Email is already registered.")
            if self.repository.username_exists(data.username):
                raise RegistrationConflict("Username is already registered.")

            password_hash = PasswordHash.recommended().hash(data.password.get_secret_value())
            user = User(
                email=str(data.email),
                username=data.username,
                password_hash=password_hash,
                first_name=data.first_name,
                last_name=data.last_name,
                phone=data.phone,
                birth_date=data.birth_date,
            )
            self.repository.add(user)
            # Build the safe response before commit expires ORM attributes.
            result = UserResponse.model_validate(user)
            self.db.commit()
            return result
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
