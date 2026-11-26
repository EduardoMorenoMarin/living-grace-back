from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.repositories.equipment import EquipmentRepository
from app.services.equipment import EquipmentLoanService, EquipmentService


def get_equipment_service(db: Annotated[Session, Depends(get_db)]) -> EquipmentService:
    return EquipmentService(EquipmentRepository(db))


def get_equipment_loan_service(db: Annotated[Session, Depends(get_db)]) -> EquipmentLoanService:
    return EquipmentLoanService(EquipmentRepository(db))
