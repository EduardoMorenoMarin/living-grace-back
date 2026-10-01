from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember


class MembershipRepository:
    """Read registration references and persist memberships in the caller's transaction."""

    def __init__(self, db: Session):
        self.db = db

    def get_ministry(self, ministry_id: int) -> Ministry | None:
        return self.db.scalar(select(Ministry).where(Ministry.id == ministry_id))

    def get_functions(self, function_ids: list[int]) -> list[Function]:
        if not function_ids:
            return []
        return list(self.db.scalars(select(Function).where(Function.id.in_(function_ids))))

    def add(self, membership: MinistryMember) -> None:
        self.db.add(membership)
        self.db.flush()
