from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.attendance_check_in import AttendanceCheckInRepository
from app.services.attendance_check_in import AttendanceCheckInService


def get_attendance_check_in_service(db: Annotated[Session, Depends(get_db)]) -> AttendanceCheckInService:
    return AttendanceCheckInService(AttendanceCheckInRepository(db))
