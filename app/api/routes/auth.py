from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.login import get_login_service
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.login import InvalidCredentials, LoginService

router = APIRouter(prefix="/auth", tags=["auth"])


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
