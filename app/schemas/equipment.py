from datetime import timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import EquipmentCondition, EquipmentStatus
from app.schemas.membership import DatabaseId


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\x00]+$")]
Text = Annotated[str, StringConstraints(pattern=r"^[^\x00]*$")]


class EquipmentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Text | None = None
    brand: Text | None = None
    model: Text | None = None
    serial_number: Text | None = None


class EquipmentCreate(EquipmentFields):
    ministry_id: DatabaseId
    name: NonEmpty
    category: NonEmpty
    condition: EquipmentCondition = EquipmentCondition.GOOD


class EquipmentUpdate(EquipmentFields):
    name: NonEmpty | None = None
    category: NonEmpty | None = None
    condition: EquipmentCondition | None = None
    status: EquipmentStatus | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one editable field must be provided.")
        for field in self.model_fields_set & {"name", "category", "condition", "status"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} must not be null.")
        return self


QueryId = Annotated[int, Field(gt=0, le=9223372036854775807)]


class Pagination(BaseModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0, le=9223372036854775807)] = 0


class InventoryFilters(Pagination):
    ministry_id: QueryId | None = None
    status: EquipmentStatus | None = None
    condition: EquipmentCondition | None = None
    category: Text | None = None
    search: Annotated[Text | None, Field(
        description="Case-insensitive substring of name, brand, model, or serial number.",
    )] = None


class LoanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ministry_member_id: DatabaseId
    expected_return_at: AwareDatetime | None = None
    notes: Text | None = None


class LoanReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_at_return: EquipmentCondition
    return_notes: Text | None = None


class LoanFilters(Pagination):
    ministry_id: QueryId | None = None
    equipment_id: QueryId | None = None
    ministry_member_id: QueryId | None = None
    active: bool | None = None
    overdue: Annotated[bool | None, Field(
        description="Filter by whether the loan is open and its expected return time has passed.",
    )] = None


UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc))]


class EquipmentMinistry(BaseModel):
    id: int
    name: str


class EquipmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    brand: str | None
    model: str | None


class EquipmentResponse(EquipmentSummary):
    ministry: EquipmentMinistry
    description: str | None
    serial_number: str | None
    status: EquipmentStatus
    condition: EquipmentCondition
    created_by: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


class EquipmentBorrower(BaseModel):
    ministry_member_id: int
    first_name: str
    last_name: str


class PersonalLoanResponse(BaseModel):
    id: int
    equipment: EquipmentSummary
    ministry: EquipmentMinistry
    borrowed_at: UtcDatetime
    expected_return_at: UtcDatetime | None
    returned_at: UtcDatetime | None
    condition_at_loan: EquipmentCondition
    condition_at_return: EquipmentCondition | None
    overdue: bool


class LoanResponse(PersonalLoanResponse):
    borrower: EquipmentBorrower
    assigned_by: int
    returned_by: int | None
    notes: str | None
    return_notes: str | None


class EquipmentDetail(EquipmentResponse):
    active_loan: LoanResponse | None


class EquipmentPage(BaseModel):
    items: list[EquipmentResponse]
    total: int


class LoanPage(BaseModel):
    items: list[LoanResponse]
    total: int
