from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import RehearsalAttendanceConflict, RehearsalPersistenceError
from app.models.rehearsal_qr_session import RehearsalQrSession


class RehearsalQrSessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def has_non_revoked(self, rehearsal_id: int) -> bool:
        return self.db.scalar(select(RehearsalQrSession.id).where(
            RehearsalQrSession.rehearsal_id == rehearsal_id,
            RehearsalQrSession.revoked_at.is_(None),
        ).limit(1)) is not None

    def add(self, session: RehearsalQrSession) -> None:
        self.db.add(session)
        self.db.flush()

    def get_active_metadata(self, rehearsal_id: int, now: datetime):
        return self.db.execute(select(
            RehearsalQrSession.id, RehearsalQrSession.rehearsal_id,
            RehearsalQrSession.opens_at, RehearsalQrSession.expires_at,
            RehearsalQrSession.created_at,
        ).where(
            RehearsalQrSession.rehearsal_id == rehearsal_id,
            RehearsalQrSession.revoked_at.is_(None),
            RehearsalQrSession.opens_at <= now, RehearsalQrSession.expires_at > now,
        )).mappings().one_or_none()

    def get_non_revoked_for_update(self, rehearsal_id: int) -> RehearsalQrSession | None:
        return self.db.scalar(select(RehearsalQrSession).where(
            RehearsalQrSession.rehearsal_id == rehearsal_id,
            RehearsalQrSession.revoked_at.is_(None),
        ).with_for_update().execution_options(populate_existing=True))

    def revoke(self, session: RehearsalQrSession, now: datetime) -> None:
        session.revoked_at = now
        self.db.flush()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            # Do not depend on manually assigned constraint names. Unique
            # violations also cover an extremely unlikely token hash collision.
            if getattr(exc.orig, "sqlstate", None) == "23505" or getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE":
                raise RehearsalAttendanceConflict("A non-revoked QR session or token already exists.") from None
            raise RehearsalPersistenceError from None
        except SQLAlchemyError:
            self.db.rollback()
            raise RehearsalPersistenceError from None
        except Exception:
            self.db.rollback()
            raise
