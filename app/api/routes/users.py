from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

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
from app.core.exceptions import (
    PasswordChangePersistenceError, PasswordResetUserIneligible, PasswordResetUserNotFound,
)
from app.dependencies.authorization import require_global_admin
from app.dependencies.reset_password import get_reset_password_service
from app.schemas.user import PasswordResetResponse
from app.services.reset_password import ResetPasswordService


router = APIRouter(prefix="/users", tags=["users"])


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    admin: Annotated[User, Depends(require_global_admin)],
    service: Annotated[ResetPasswordService, Depends(get_reset_password_service)],
) -> PasswordResetResponse:
    try:
        return service.reset(user_id)
    except PasswordResetUserNotFound:
        raise HTTPException(status_code=404, detail="User does not exist.") from None
    except PasswordResetUserIneligible:
        raise HTTPException(status_code=409, detail="Only active users can have their password reset.") from None
    except PasswordChangePersistenceError:
        raise HTTPException(status_code=500, detail="Unable to reset password.") from None


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
