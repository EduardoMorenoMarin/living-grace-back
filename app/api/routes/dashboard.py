from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.exceptions import DashboardReadError, MinistryNotFound
from app.dependencies.auth import get_current_user
from app.dependencies.dashboard import get_dashboard_service
from app.dependencies.rehearsal_read import get_rehearsal_viewer
from app.models.user import User
from app.schemas.dashboard import AdminDashboardResponse, DashboardResponse
from app.services.dashboard import DashboardService
from app.services.rehearsal_read import RehearsalViewer


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/me", response_model=DashboardResponse | AdminDashboardResponse)
def get_my_dashboard(
    user: Annotated[User, Depends(get_current_user)],
    viewer: Annotated[RehearsalViewer, Depends(get_rehearsal_viewer)],
    service: Annotated[DashboardService, Depends(get_dashboard_service)],
    ministry_id: Annotated[int | None, Query(gt=0, le=9223372036854775807)] = None,
) -> DashboardResponse | AdminDashboardResponse:
    try:
        return service.get(user, viewer, ministry_id)
    except MinistryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except DashboardReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve dashboard.") from None
