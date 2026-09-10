from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import RegistrationConflict, RegistrationPersistenceError
from app.dependencies.registration import get_registration_service
from app.schemas.user import UserCreate, UserResponse
from app.services.registration import RegistrationService


router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    data: UserCreate,
    service: Annotated[RegistrationService, Depends(get_registration_service)],
) -> UserResponse:
    try:
        return service.register(data)
    except RegistrationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except RegistrationPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to register user.",
        ) from None
