from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import RegistrationConflict, RegistrationPersistenceError, RegistrationValidationError
from app.dependencies.registration import get_registration_service
from app.schemas.user import UserCreate, UserResponse
from app.services.registration import RegistrationService
from app.core.exceptions import ProfilePersistenceError, ProfileUserUnavailable
from app.dependencies.auth import get_current_user
from app.dependencies.profile import get_profile_service
from app.models.user import User
from app.schemas.profile import UserProfile
from app.services.profile import ProfileService


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfile)
def get_my_profile(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> UserProfile:
    try:
        return service.get_profile(user.id)
    except ProfileUserUnavailable:
        raise HTTPException(
            status_code=401, detail="Invalid or missing access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except ProfilePersistenceError:
        raise HTTPException(status_code=500, detail="Unable to retrieve profile.") from None


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    data: UserCreate,
    service: Annotated[RegistrationService, Depends(get_registration_service)],
) -> UserResponse:
    try:
        return service.register(data)
    except RegistrationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except RegistrationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except RegistrationPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to register user.",
        ) from None
