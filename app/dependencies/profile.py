from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.profile import ProfileRepository
from app.services.profile import ProfileService


def get_profile_service(db: Annotated[Session, Depends(get_db)]) -> ProfileService:
    return ProfileService(ProfileRepository(db))
