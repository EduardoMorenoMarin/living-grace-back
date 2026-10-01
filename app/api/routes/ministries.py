from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies.ministries import get_ministry_registration_service
from app.schemas.ministry import MinistryRegistrationInfo
from app.services.ministry_registration import MinistryRegistrationService

router = APIRouter(prefix="/ministries", tags=["ministries"])


@router.get("/registration-fields", response_model=list[MinistryRegistrationInfo])
def list_ministries_for_registration(
    service: Annotated[MinistryRegistrationService, Depends(get_ministry_registration_service)],
) -> list[MinistryRegistrationInfo]:
    return service.list_for_registration()