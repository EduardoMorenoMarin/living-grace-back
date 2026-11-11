from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies.database import get_db
from app.repositories.rehearsal_qr_session import RehearsalQrSessionRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.rehearsal_qr_session import RehearsalQrSessionService


def get_rehearsal_qr_session_service(db: Annotated[Session, Depends(get_db)]) -> RehearsalQrSessionService:
    return RehearsalQrSessionService(
        RehearsalQrSessionRepository(db), RehearsalReadRepository(db), settings.FRONTEND_ORIGIN,
    )
