import re
from datetime import date, datetime, time, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BeforeValidator

from app.core.config import settings
from app.core.exceptions import RehearsalReadError
from app.dependencies.rehearsal_read import get_rehearsal_read_service, get_rehearsal_viewer
from app.schemas.calendar import CalendarRehearsal
from app.services.rehearsal_read import RehearsalReadService, RehearsalViewer


router = APIRouter(prefix="/calendar", tags=["calendar"])


def parse_calendar_date(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use YYYY-MM-DD.")
    return date.fromisoformat(value)


CalendarDate = Annotated[date, BeforeValidator(parse_calendar_date)]


def utc_midnight(value: date | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.combine(value, time.min, ZoneInfo(settings.APP_TIMEZONE)).astimezone(timezone.utc)
    except OverflowError:
        raise RequestValidationError([
            {"loc": ("query", field), "msg": "Date is outside the supported UTC range.", "type": "value_error"},
        ]) from None


@router.get(
    "/rehearsals",
    response_model=list[CalendarRehearsal],
    summary="List upcoming rehearsals for the calendar",
    description=(
        "Returns rehearsals visible to the authenticated user, ordered by start time. "
        "A rehearsal is upcoming if it starts at or after the current time. "
        "Both date bounds are optional. When supplied, the rehearsal must overlap the requested range. "
        "Invalid dates and equal or reversed bounds return 422."
    ),
    responses={
        401: {
            "description": "Invalid or missing access token, or ineligible account.",
            "headers": {"WWW-Authenticate": {"schema": {"type": "string", "example": "Bearer"}}},
        },
        403: {"description": "Password change required or no eligible global ADMIN or USER role."},
        500: {"description": "Unable to authenticate, authorize, or retrieve calendar rehearsals."},
    },
)
def list_calendar_rehearsals(
    viewer: Annotated[RehearsalViewer, Depends(get_rehearsal_viewer)],
    service: Annotated[RehearsalReadService, Depends(get_rehearsal_read_service)],
    start: Annotated[CalendarDate | None, Query(
        description="Inclusive start at midnight in APP_TIMEZONE (YYYY-MM-DD).",
    )] = None,
    end: Annotated[CalendarDate | None, Query(
        description="Exclusive end at midnight in APP_TIMEZONE (YYYY-MM-DD). Must follow start when both are supplied.",
    )] = None,
) -> list[CalendarRehearsal]:
    if start is not None and end is not None and end <= start:
        raise RequestValidationError([
            {"loc": ("query", "end"), "msg": "End must be after start.", "type": "value_error"},
        ])
    try:
        return service.list_calendar(viewer, utc_midnight(start, "start"), utc_midnight(end, "end"))
    except RehearsalReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve calendar rehearsals.") from None
