from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MinistryStatus
from app.models.ministry import Ministry


class MinistryRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_active(self) -> list[Ministry]:
        return list(
            self.db.scalars(
                select(Ministry).where(
                    Ministry.status == MinistryStatus.ACTIVE,
                    Ministry.deleted_at.is_(None),
                )
            )
        )