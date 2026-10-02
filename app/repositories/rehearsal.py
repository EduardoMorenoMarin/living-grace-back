from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RehearsalPersistenceError
from app.models.rehearsal import Rehearsal


class RehearsalRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, rehearsal: Rehearsal) -> None:
        self.db.add(rehearsal)
        self.db.flush()
        self.db.refresh(rehearsal)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise RehearsalPersistenceError("Unable to create rehearsal.") from None
        except Exception:
            self.db.rollback()
            raise
