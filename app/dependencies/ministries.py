from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.ministry import MinistryRepository
from app.services.ministry_fields.registry import MinistryFieldsRegistry
from app.services.ministry_registration import MinistryRegistrationService


def get_ministry_registration_service(
    db: Annotated[Session, Depends(get_db)],
) -> MinistryRegistrationService:
    return MinistryRegistrationService(MinistryRepository(db), MinistryFieldsRegistry())