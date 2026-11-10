from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.exceptions import (
    AttendanceCheckInForbidden, AttendanceCheckInPersistenceError,
    AttendanceSessionInvalid, RehearsalAttendanceConflict,
)
from app.dependencies.attendance_check_in import get_attendance_check_in_service
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.attendance_check_in import AttendanceCheckIn, AttendanceCheckInResponse
from app.services.attendance_check_in import AttendanceCheckInService


router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.post("/check-in", response_model=AttendanceCheckInResponse, status_code=201)
def check_in(
    data: AttendanceCheckIn,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[AttendanceCheckInService, Depends(get_attendance_check_in_service)],
    response: Response,
) -> AttendanceCheckInResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result, created = service.check_in(user.id, data)
    except AttendanceSessionInvalid:
        raise HTTPException(status_code=400, detail="Invalid or unavailable attendance session.") from None
    except AttendanceCheckInForbidden:
        raise HTTPException(status_code=403, detail="Attendance check-in is not permitted.") from None
    except RehearsalAttendanceConflict:
        raise HTTPException(status_code=409, detail="Attendance already registered.") from None
    except AttendanceCheckInPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to register attendance.") from None
    response.status_code = 201 if created else 200
    return result
