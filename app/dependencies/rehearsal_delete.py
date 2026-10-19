from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.rehearsal import RehearsalRepository
from app.repositories.rehearsal_read import RehearsalReadRepository
from app.services.rehearsal_delete import RehearsalDeleteService


def get_rehearsal_delete_service(db: Annotated[Session, Depends(get_db)]) -> RehearsalDeleteService:
    return RehearsalDeleteService(RehearsalRepository(db), RehearsalReadRepository(db))
