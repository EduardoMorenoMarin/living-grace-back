from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.ministry_members import MinistryMembersRepository
from app.services.ministry_members import MinistryMembersService


def get_ministry_members_service(db: Annotated[Session, Depends(get_db)]) -> MinistryMembersService:
    return MinistryMembersService(MinistryMembersRepository(db))
