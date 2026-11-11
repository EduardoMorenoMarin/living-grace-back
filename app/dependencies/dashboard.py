from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.dependencies.database import get_db
from app.repositories.dashboard import DashboardRepository
from app.services.dashboard import DashboardService


def get_dashboard_service(db: Annotated[Session, Depends(get_db)]) -> DashboardService:
    return DashboardService(DashboardRepository(db), settings.APP_TIMEZONE)
