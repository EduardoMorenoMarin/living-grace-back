from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status

from app.core.exceptions import RehearsalNotFound, RehearsalReadError
from app.dependencies.rehearsal_read import get_rehearsal_read_service, get_rehearsal_viewer
from app.schemas.rehearsal_read import AdminRehearsalDetail, AdminRehearsalPage, RehearsalInfo, RehearsalPage
from app.services.rehearsal_read import RehearsalReadService, RehearsalViewer

from app.core.exceptions import MinistryNotFound, RehearsalPersistenceError, RehearsalValidationError
from app.dependencies.authorization import require_global_admin
from app.dependencies.rehearsals import get_rehearsal_creator
from app.models.user import User
from app.schemas.rehearsal import RehearsalCreate, RehearsalResponse
from app.services.rehearsal_creation import RehearsalCreator
from app.core.exceptions import RehearsalAttendanceConflict
from app.dependencies.rehearsal_update import get_rehearsal_update_service
from app.schemas.rehearsal_update import RehearsalUpdate
from app.services.rehearsal_update import RehearsalUpdateService
from app.dependencies.rehearsal_delete import get_rehearsal_delete_service
from app.services.rehearsal_delete import RehearsalDeleteService


router = APIRouter(prefix="/rehearsals", tags=["rehearsals"])


@router.delete("/{rehearsal_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_rehearsal(
    rehearsal_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    user: Annotated[User, Depends(require_global_admin)],
    service: Annotated[RehearsalDeleteService, Depends(get_rehearsal_delete_service)],
) -> Response:
    try:
        service.delete(rehearsal_id, user.id)
    except RehearsalNotFound:
        raise HTTPException(status_code=404, detail="Rehearsal does not exist.") from None
    except RehearsalAttendanceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except RehearsalPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to delete rehearsal.") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{rehearsal_id}", response_model=AdminRehearsalDetail)
def update_rehearsal(
    rehearsal_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    data: RehearsalUpdate,
    user: Annotated[User, Depends(require_global_admin)],
    service: Annotated[RehearsalUpdateService, Depends(get_rehearsal_update_service)],
) -> AdminRehearsalDetail:
    try:
        return service.update(rehearsal_id, user.id, data)
    except RehearsalNotFound:
        raise HTTPException(status_code=404, detail="Rehearsal does not exist.") from None
    except RehearsalValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RehearsalAttendanceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (RehearsalPersistenceError, RehearsalReadError):
        raise HTTPException(status_code=500, detail="Unable to update rehearsal.") from None


@router.get("", response_model=AdminRehearsalPage | RehearsalPage)
def list_rehearsals(
    viewer: Annotated[RehearsalViewer, Depends(get_rehearsal_viewer)],
    service: Annotated[RehearsalReadService, Depends(get_rehearsal_read_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    ministry_id: Annotated[int | None, Query(gt=0, le=9223372036854775807)] = None,
) -> AdminRehearsalPage | RehearsalPage:
    try:
        return service.list_visible(viewer, limit, offset, ministry_id)
    except RehearsalReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve rehearsals.") from None


@router.get("/{rehearsal_id}", response_model=AdminRehearsalDetail | RehearsalInfo)
def get_rehearsal(
    rehearsal_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    viewer: Annotated[RehearsalViewer, Depends(get_rehearsal_viewer)],
    service: Annotated[RehearsalReadService, Depends(get_rehearsal_read_service)],
) -> AdminRehearsalDetail | RehearsalInfo:
    try:
        return service.get_visible(viewer, rehearsal_id)
    except RehearsalNotFound:
        raise HTTPException(status_code=404, detail="Rehearsal does not exist.") from None
    except RehearsalReadError:
        raise HTTPException(status_code=500, detail="Unable to retrieve rehearsal.") from None


@router.post("", response_model=RehearsalResponse, status_code=status.HTTP_201_CREATED)
def create_rehearsal(
    data: RehearsalCreate,
    user: Annotated[User, Depends(require_global_admin)],
    creator: Annotated[RehearsalCreator, Depends(get_rehearsal_creator)],
) -> RehearsalResponse:
    try:
        return creator.create(data, user.id)
    except MinistryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RehearsalValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RehearsalPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to create rehearsal.") from None
