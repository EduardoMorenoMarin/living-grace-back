from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.dependencies.login import get_login_service
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.login import InvalidCredentials, LoginService
from app.core.exceptions import (
    CurrentPasswordIncorrect, PasswordChangePersistenceError, PasswordChangeValidationError,
)
from app.dependencies.auth import get_authenticated_user
from app.dependencies.change_password import get_change_password_service
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest
from app.services.change_password import ChangePasswordService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def change_password(
    data: ChangePasswordRequest,
    user: Annotated[User, Depends(get_authenticated_user)],
    service: Annotated[ChangePasswordService, Depends(get_change_password_service)],
) -> Response:
    try:
        service.change(user.id, data)
    except CurrentPasswordIncorrect:
        raise HTTPException(status_code=400, detail="Current password is incorrect.") from None
    except PasswordChangeValidationError:
        raise HTTPException(status_code=422, detail="New password must differ from the current password.") from None
    except PasswordChangePersistenceError:
        raise HTTPException(status_code=500, detail="Unable to change password.") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/login", response_model=TokenResponse)
def login(
    data: LoginRequest,
    service: Annotated[LoginService, Depends(get_login_service)],
) -> TokenResponse:
    try:
        return service.login(data)
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from None
