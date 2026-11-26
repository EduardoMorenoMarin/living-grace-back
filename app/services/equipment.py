from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.engine import Row
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import EquipmentConflict, EquipmentNotFound, EquipmentPersistenceError, EquipmentValidationError
from app.models.enums import EquipmentCondition, EquipmentStatus
from app.models.equipment import Equipment
from app.models.equipment_loan import EquipmentLoan
from app.models.ministry import Ministry
from app.repositories.equipment import EquipmentRepository
from app.schemas.equipment import (
    EquipmentBorrower, EquipmentCreate, EquipmentDetail, EquipmentMinistry, EquipmentPage, EquipmentResponse,
    EquipmentSummary, EquipmentUpdate, InventoryFilters, LoanCreate, LoanFilters, LoanPage, LoanResponse, LoanReturn,
    PersonalLoanResponse,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def equipment_response(item: Equipment, ministry: Ministry) -> EquipmentResponse:
    return EquipmentResponse(
        **EquipmentSummary.model_validate(item).model_dump(), ministry=EquipmentMinistry(id=ministry.id, name=ministry.name),
        description=item.description, serial_number=item.serial_number, status=item.status, condition=item.condition,
        created_by=item.created_by, created_at=item.created_at, updated_at=item.updated_at,
    )


def personal_loan_response(row: Row) -> PersonalLoanResponse:
    loan, item, ministry, _, _, overdue = row
    return PersonalLoanResponse(
        id=loan.id, equipment=EquipmentSummary.model_validate(item),
        ministry=EquipmentMinistry(id=ministry.id, name=ministry.name),
        borrowed_at=loan.borrowed_at, expected_return_at=loan.expected_return_at, returned_at=loan.returned_at,
        condition_at_loan=loan.condition_at_loan, condition_at_return=loan.condition_at_return, overdue=overdue,
    )


def loan_response(row: Row) -> LoanResponse:
    loan = row.EquipmentLoan
    return LoanResponse(
        **personal_loan_response(row).model_dump(),
        borrower=EquipmentBorrower(
            ministry_member_id=loan.ministry_member_id, first_name=row.first_name, last_name=row.last_name,
        ),
        assigned_by=loan.assigned_by, returned_by=loan.returned_by, notes=loan.notes, return_notes=loan.return_notes,
    )


def usable_status(condition: EquipmentCondition) -> EquipmentStatus:
    return EquipmentStatus.MAINTENANCE if condition == EquipmentCondition.DAMAGED else EquipmentStatus.AVAILABLE


@contextmanager
def equipment_read() -> Iterator[None]:
    try:
        yield
    except SQLAlchemyError:
        raise EquipmentPersistenceError("Unable to retrieve equipment or loans.") from None


class EquipmentService:
    def __init__(self, repository: EquipmentRepository):
        self.repository = repository

    def create(self, user_id: int, data: EquipmentCreate) -> EquipmentResponse:
        with self.repository.transaction():
            ministry = self.repository.get_managed_ministry(user_id, data.ministry_id)
            if ministry is None:
                raise EquipmentNotFound("Ministry does not exist.")
            item = Equipment(**data.model_dump(), created_by=user_id, status=usable_status(data.condition))
            self.repository.add(item)
            return equipment_response(item, ministry)

    def list_inventory(self, user_id: int, filters: InventoryFilters) -> EquipmentPage:
        with equipment_read():
            rows, total = self.repository.list_inventory(user_id, filters)
            return EquipmentPage(items=[equipment_response(item, ministry) for item, ministry in rows], total=total)

    def get(self, user_id: int, equipment_id: int) -> EquipmentDetail:
        with equipment_read():
            row = self.repository.equipment_detail(user_id, equipment_id, utc_now())
            if row is None:
                raise EquipmentNotFound("Equipment does not exist.")
            return EquipmentDetail(
                **equipment_response(row.Equipment, row.Ministry).model_dump(),
                active_loan=loan_response(row) if row.EquipmentLoan is not None else None,
            )

    def update(self, user_id: int, equipment_id: int, data: EquipmentUpdate) -> EquipmentResponse:
        with self.repository.transaction():
            row = self.repository.lock_equipment(user_id, equipment_id)
            if row is None:
                raise EquipmentNotFound("Equipment does not exist.")
            item, ministry = row
            changes = data.model_dump(exclude_unset=True)
            self._validate_update(item, changes)
            for field, value in changes.items():
                setattr(item, field, value)
            item.updated_at = utc_now()
            return equipment_response(item, ministry)

    def _validate_update(self, item: Equipment, changes: dict) -> None:
        status = changes.get("status", item.status)
        condition = changes.get("condition", item.condition)
        if changes.get("status") == EquipmentStatus.BORROWED:
            raise EquipmentConflict("Borrowed status is managed through loans.")
        if item.status == EquipmentStatus.RETIRED and status != item.status:
            raise EquipmentConflict("Retired equipment cannot be reactivated.")
        if status != item.status or condition != item.condition:
            if item.status == EquipmentStatus.BORROWED or self.repository.has_active_loan(item.id):
                raise EquipmentConflict("Status and condition cannot change during an active loan.")
            if status == EquipmentStatus.AVAILABLE and condition == EquipmentCondition.DAMAGED:
                if "status" in changes:
                    raise EquipmentConflict("Damaged equipment cannot be available.")
                changes["status"] = EquipmentStatus.MAINTENANCE

    def delete(self, user_id: int, equipment_id: int) -> None:
        with self.repository.transaction():
            row = self.repository.lock_equipment(user_id, equipment_id)
            if row is None:
                raise EquipmentNotFound("Equipment does not exist.")
            item = row.Equipment
            if self.repository.has_active_loan(item.id):
                raise EquipmentConflict("Equipment with an active loan cannot be deleted.")
            item.status = EquipmentStatus.RETIRED
            item.deleted_at = item.updated_at = utc_now()


class EquipmentLoanService:
    def __init__(self, repository: EquipmentRepository):
        self.repository = repository

    def create(self, user_id: int, equipment_id: int, data: LoanCreate) -> LoanResponse:
        with self.repository.transaction():
            row = self.repository.lock_equipment(user_id, equipment_id)
            if row is None:
                raise EquipmentNotFound("Equipment does not exist.")
            item = row.Equipment
            if item.status != EquipmentStatus.AVAILABLE or item.condition == EquipmentCondition.DAMAGED:
                raise EquipmentConflict("Equipment is unavailable for borrowing.")
            if self.repository.has_active_loan(item.id):
                raise EquipmentConflict("Equipment already has an active loan.")
            if not self.repository.eligible_borrower(item.ministry_id, data.ministry_member_id):
                raise EquipmentNotFound("Borrower is unavailable in this ministry.")
            now = utc_now()
            if data.expected_return_at is not None and data.expected_return_at <= now:
                raise EquipmentValidationError("Expected return must be after the borrowing time.")
            loan = EquipmentLoan(
                **data.model_dump(), equipment_id=item.id, assigned_by=user_id, borrowed_at=now,
                condition_at_loan=item.condition,
            )
            item.status = EquipmentStatus.BORROWED
            item.updated_at = now
            self.repository.add(loan)
            return loan_response(self.repository.loan_detail(loan.id, now))

    def return_loan(self, user_id: int, loan_id: int, data: LoanReturn) -> LoanResponse:
        with self.repository.transaction():
            # Match the equipment-first lock order used for loan creation and equipment changes.
            row = self.repository.lock_equipment(
                user_id, self.repository.equipment_id_for_loan(loan_id), include_deleted=True,
            )
            if row is None:
                raise EquipmentNotFound("Loan does not exist.")
            item = row.Equipment
            loan = self.repository.loan_for_update(item.id, loan_id)
            if loan is None:
                raise EquipmentNotFound("Loan does not exist.")
            if loan.returned_at is not None:
                raise EquipmentConflict("Loan has already been returned.")
            if item.deleted_at is not None:
                raise EquipmentConflict("Deleted equipment cannot be returned.")
            now = max(utc_now(), loan.borrowed_at)
            loan.returned_at = now
            loan.returned_by = user_id
            loan.condition_at_return = data.condition_at_return
            loan.return_notes = data.return_notes
            item.condition = data.condition_at_return
            item.status = usable_status(data.condition_at_return)
            item.updated_at = now
            self.repository.flush()
            return loan_response(self.repository.loan_detail(loan.id, now))

    def history(self, user_id: int, equipment_id: int) -> list[LoanResponse]:
        with equipment_read():
            rows = self.repository.equipment_history(user_id, equipment_id, utc_now())
            if not rows:
                raise EquipmentNotFound("Equipment does not exist.")
            return [loan_response(row) for row in rows if row.EquipmentLoan is not None]

    def list_loans(self, user_id: int, filters: LoanFilters) -> LoanPage:
        with equipment_read():
            rows, total = self.repository.list_loans(user_id, filters, utc_now())
            return LoanPage(items=[loan_response(row) for row in rows], total=total)

    def own_history(self, user_id: int, active: bool | None) -> list[PersonalLoanResponse]:
        with equipment_read():
            return [personal_loan_response(row) for row in self.repository.own_history(user_id, active, utc_now())]
