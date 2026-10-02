from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import MinistryNotFound, RehearsalPersistenceError
from app.dependencies.auth import get_current_user
from app.dependencies.rehearsals import get_rehearsal_creator
from app.models.user import User
from app.schemas.rehearsal import RehearsalCreate, RehearsalResponse
from app.services.rehearsal_creation import RehearsalCreator


router = APIRouter(prefix="/rehearsals", tags=["rehearsals"])


@router.post("", response_model=RehearsalResponse, status_code=status.HTTP_201_CREATED)
def create_rehearsal(
    data: RehearsalCreate,
    user: Annotated[User, Depends(get_current_user)],
    creator: Annotated[RehearsalCreator, Depends(get_rehearsal_creator)],
) -> RehearsalResponse:
    try:
        return creator.create(data, user.id)
    except MinistryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RehearsalPersistenceError:
        raise HTTPException(status_code=500, detail="Unable to create rehearsal.") from None
