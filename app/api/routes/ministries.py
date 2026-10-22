from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.core.exceptions import (MembershipNotFound, MembershipTransitionConflict,
                                 MinistryMembersPersistenceError, MinistryNotFound)
from app.models.enums import MembershipStatus
from app.dependencies.authorization import require_global_admin
from app.dependencies.ministry_members import get_ministry_members_service
from app.schemas.ministry_members import (MembershipStatusResponse, MembershipStatusUpdate,
                                         MinistryMembersResponse)
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
    status: Annotated[MembershipStatus, Query()] = MembershipStatus.ACTIVE,
) -> MinistryMembersResponse:
    try:
        return service.list_members(ministry_id, status)
    except MinistryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except MinistryMembersPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to retrieve ministry members.") from None


@router.patch(
    "/{ministry_id}/members/{membership_id}/status", response_model=MembershipStatusResponse,
    dependencies=[Depends(require_global_admin)],
)
def update_membership_status(
    ministry_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    membership_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    data: MembershipStatusUpdate,
    service: Annotated[MinistryMembersService, Depends(get_ministry_members_service)],
) -> MembershipStatusResponse:
    try:
        return service.approve(ministry_id, membership_id)
    except MembershipNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except MembershipTransitionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except MinistryMembersPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to update membership status.") from None
