from collections.abc import Iterator
from contextlib import contextmanager
import logging
import traceback

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RegistrationConflict, RegistrationPersistenceError
from app.core.database import engine
from app.models.user import User

from sqlalchemy import or_, select

logger = logging.getLogger(__name__)


def _log_persistence_error(exc: SQLAlchemyError) -> None:
    original = getattr(exc, "orig", exc)
    diagnostic = getattr(original, "diag", None)
    # PostgreSQL DETAIL can contain the entire failing row, including its hash.
    # Do not stringify the SQLAlchemy wrapper: it can include bound parameters.
    message = getattr(diagnostic, "message_primary", None)
    if not message:
        message = str(original).splitlines()[0] if str(original) else type(original).__name__
    # Connection errors may contain a connection URL or password.
    for secret in (engine.url.render_as_string(hide_password=False), engine.url.password):
        if secret:
            message = message.replace(secret, "[redacted]")
    logger.error(
        "Registration persistence failed: %s / %s; SQLSTATE=%s; %s\n%s",
        type(exc).__name__,
        type(original).__name__,
        getattr(original, "sqlstate", None),
        message,
        "".join(traceback.format_tb(exc.__traceback__)),
    )


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
            _log_persistence_error(exc)
            self.db.rollback()
            # A concurrent request can insert after the uniqueness checks.
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise RegistrationConflict("Email or username is already registered.") from None
            raise RegistrationPersistenceError("Unable to register user.") from None
        except SQLAlchemyError as exc:
            _log_persistence_error(exc)
            self.db.rollback()
            raise RegistrationPersistenceError("Unable to register user.") from None

    def get_by_identifier(self, identifier: str) -> User | None:
        """Fetch a user by email or username, whichever matches."""
        return self.db.scalar(
            select(User).where(or_(User.email == identifier, User.username == identifier))
        )
