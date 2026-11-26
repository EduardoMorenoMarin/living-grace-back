from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.exceptions import EquipmentConflict, EquipmentNotFound, EquipmentPersistenceError, EquipmentValidationError
from app.dependencies.auth import get_current_user
from app.dependencies.authorization import require_global_admin
from app.dependencies.equipment import get_equipment_loan_service, get_equipment_service
from app.models.user import User
from app.schemas.equipment import (
    EquipmentCreate, EquipmentDetail, EquipmentPage, EquipmentResponse, EquipmentUpdate, InventoryFilters,
    LoanCreate, LoanFilters, LoanPage, LoanResponse, LoanReturn, PersonalLoanResponse,
)
from app.services.equipment import EquipmentLoanService, EquipmentService


class EquipmentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except EquipmentNotFound as exc:
                raise HTTPException(404, str(exc)) from None
            except EquipmentConflict as exc:
                raise HTTPException(409, str(exc)) from None
            except EquipmentValidationError as exc:
                raise HTTPException(422, str(exc)) from None
            except EquipmentPersistenceError as exc:
                raise HTTPException(500, str(exc)) from None

        return handle


class EquipmentErrorResponse(BaseModel):
    detail: str


class EquipmentValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class LoanCreateError(BaseModel):
    detail: str | list[EquipmentValidationIssue]


def error(description: str):
    return {"description": description, "model": EquipmentErrorResponse}


router = APIRouter(
    prefix="/equipment", tags=["equipment"], route_class=EquipmentRoute,
    responses={
        401: error("Missing or invalid bearer token, or ineligible account."),
        403: error("Required role missing or password change required."),
        500: error("Authentication, authorization or database failure."),
    },
)
PathId = Annotated[int, Path(gt=0, le=9223372036854775807)]
Admin = Annotated[User, Depends(require_global_admin)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Inventory = Annotated[EquipmentService, Depends(get_equipment_service)]
Loans = Annotated[EquipmentLoanService, Depends(get_equipment_loan_service)]
SCOPED_ADMIN = "Requires global ADMIN and active membership in the item's active ministry."
ITEM_HIDDEN = error("Equipment is missing, soft-deleted, or outside the caller's authorized ministry scope.")


@router.post(
    "", status_code=201,
    description="Requires global ADMIN and active membership in the selected active ministry. "
                "Registers one physical item. Damaged equipment starts in MAINTENANCE.",
    responses={404: error("The ministry is unavailable or outside the caller's authorized scope.")},
)
def create_equipment(data: EquipmentCreate, user: Admin, service: Inventory) -> EquipmentResponse:
    return service.create(user.id, data)


@router.get(
    "",
    description="Global ADMIN only. Lists non-deleted equipment within authorized ministries, ordered by name and ID.",
)
def list_equipment(user: Admin, service: Inventory, filters: Annotated[InventoryFilters, Query()]) -> EquipmentPage:
    return service.list_inventory(user.id, filters)


@router.get(
    "/loans",
    description="Global ADMIN only. Lists active and completed loans in authorized ministries, newest first.",
)
def list_equipment_loans(user: Admin, service: Loans, filters: Annotated[LoanFilters, Query()]) -> LoanPage:
    return service.list_loans(user.id, filters)


@router.get(
    "/loans/me",
    description="Returns the authenticated user's own equipment loans, newest first. "
                "History includes loans from former memberships while the account remains eligible.",
)
def list_my_equipment_loans(
    user: CurrentUser, service: Loans,
    active: Annotated[bool | None, Query(description="Filter open loans (true) or completed loans (false).")] = None,
) -> list[PersonalLoanResponse]:
    return service.own_history(user.id, active)


@router.patch(
    "/loans/{loan_id}/return",
    description=f"{SCOPED_ADMIN} Records the return and updates the equipment condition and status atomically.",
    responses={
        404: error("Loan is missing or outside the caller's authorized ministry scope."),
        409: error("The loan was already returned or the equipment is deleted."),
    },
)
def return_equipment_loan(loan_id: PathId, data: LoanReturn, user: Admin, service: Loans) -> LoanResponse:
    return service.return_loan(user.id, loan_id, data)


@router.get(
    "/{equipment_id}", description=f"{SCOPED_ADMIN} Includes the ministry and any active loan.",
    responses={404: ITEM_HIDDEN},
)
def get_equipment(equipment_id: PathId, user: Admin, service: Inventory) -> EquipmentDetail:
    return service.get(user.id, equipment_id)


@router.patch(
    "/{equipment_id}", description=f"{SCOPED_ADMIN} Partially updates editable details, status, or condition.",
    responses={404: ITEM_HIDDEN, 409: error("The requested status or condition conflicts with inventory rules.")},
)
def update_equipment(equipment_id: PathId, data: EquipmentUpdate, user: Admin, service: Inventory) -> EquipmentResponse:
    return service.update(user.id, equipment_id, data)


@router.delete(
    "/{equipment_id}", status_code=204, response_class=Response,
    description=f"{SCOPED_ADMIN} Soft-deletes and retires the equipment while preserving its history.",
    responses={404: ITEM_HIDDEN, 409: error("Equipment has an active loan.")},
)
def delete_equipment(equipment_id: PathId, user: Admin, service: Inventory) -> None:
    service.delete(user.id, equipment_id)


@router.post(
    "/{equipment_id}/loans", status_code=201,
    description=f"{SCOPED_ADMIN} Lends available equipment to an active member of the same ministry.",
    responses={
        404: error("Equipment or borrower is missing, unavailable, or outside the authorized ministry scope."),
        409: error("Equipment is unavailable or already has an active loan."),
        422: {"description": "Invalid request or expected return is no later than borrowing time.", "model": LoanCreateError},
    },
)
def create_equipment_loan(equipment_id: PathId, data: LoanCreate, user: Admin, service: Loans) -> LoanResponse:
    return service.create(user.id, equipment_id, data)


@router.get(
    "/{equipment_id}/loans",
    description=f"{SCOPED_ADMIN} Returns every loan, newest first, including for retired and soft-deleted items.",
    responses={404: error("Equipment is missing or outside the caller's authorized ministry scope.")},
)
def list_item_loans(equipment_id: PathId, user: Admin, service: Loans) -> list[LoanResponse]:
    return service.history(user.id, equipment_id)
