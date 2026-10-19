from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.rehearsal_read import RehearsalReadService
from app.services.rehearsal_update import RehearsalUpdateService


def get_rehearsal_update_service(db: Annotated[Session, Depends(get_db)]) -> RehearsalUpdateService:
    reads = RehearsalReadRepository(db)
    return RehearsalUpdateService(RehearsalRepository(db), reads, RehearsalReadService(reads))
