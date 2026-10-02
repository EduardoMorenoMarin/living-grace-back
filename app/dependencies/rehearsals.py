from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.membership import MembershipRepository
from app.repositories.rehearsal import RehearsalRepository
from app.services.rehearsal_creation import LoggingRehearsalCreator, RehearsalCreator


def get_rehearsal_creator(db: Annotated[Session, Depends(get_db)]) -> RehearsalCreator:
    return LoggingRehearsalCreator(RehearsalRepository(db), MembershipRepository(db))
