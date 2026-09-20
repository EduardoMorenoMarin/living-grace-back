from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies.database import get_db
from app.models.enums import FunctionStatus, MinistryStatus
from app.models.ministry import Ministry

router = APIRouter(prefix="/ministries", tags=["ministries"])


@router.get("/registration-fields")
def list_ministries_for_registration(db: Session = Depends(get_db)):
    ministries = db.query(Ministry).filter(Ministry.status == MinistryStatus.ACTIVE).all()
    result = []

    for ministry in ministries:
        # --- violación de OCP: cada ministerio nuevo obliga a tocar este if/elif ---
        if ministry.name == "Alabanza":
            extra_fields = [
                {"name": "main_instrument", "label": "Instrumento principal", "type": "text", "required": True},
            ]
        elif ministry.name == "Producción":
            extra_fields = [
                {"name": "technical_area", "label": "Área técnica", "type": "text", "required": True},
            ]
        else:
            extra_fields = []

        functions = [f.name for f in ministry.functions if f.status == FunctionStatus.ACTIVE]

        result.append({
            "id": ministry.id,
            "name": ministry.name,
            "description": ministry.description,
            "functions": functions,
            "extra_fields": extra_fields,
        })

    return result