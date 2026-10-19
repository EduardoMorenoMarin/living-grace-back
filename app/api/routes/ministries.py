from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from app.core.exceptions import MinistryMembersPersistenceError, MinistryNotFound
from app.dependencies.authorization import require_global_admin
from app.dependencies.ministry_members import get_ministry_members_service
from app.schemas.ministry_members import MinistryMembersResponse
from app.services.ministry_members import MinistryMembersService

from app.dependencies.ministries import get_ministry_registration_service
from app.schemas.ministry import MinistryRegistrationInfo
from app.services.ministry_registration import MinistryRegistrationService

router = APIRouter(prefix="/ministries", tags=["ministries"])


@router.get("/registration-fields", response_model=list[MinistryRegistrationInfo])
def list_ministries_for_registration(
    service: Annotated[MinistryRegistrationService, Depends(get_ministry_registration_service)],
) -> list[MinistryRegistrationInfo]:
    return service.list_for_registration()


@router.get(
    "/{ministry_id}/members", response_model=MinistryMembersResponse,
    dependencies=[Depends(require_global_admin)],
)
def list_ministry_members(
    ministry_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    service: Annotated[MinistryMembersService, Depends(get_ministry_members_service)],
) -> MinistryMembersResponse:
    try:
        return service.list_members(ministry_id)
    except MinistryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except MinistryMembersPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to retrieve ministry members.") from None
