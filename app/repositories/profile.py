from collections.abc import Sequence

from sqlalchemy import and_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import ProfilePersistenceError
from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus, UserStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_function import MinistryMemberFunction
from app.models.user import User


class ProfileRepository:
    """Read only the requested user's public profile and available assignments."""

    def __init__(self, db: Session):
        self.db = db

    def get_profile_rows(self, user_id: int) -> Sequence[RowMapping]:
        statement = (
            select(
                User.first_name, User.last_name, User.email, User.phone,
                User.birth_date, User.created_at,
                Ministry.id.label("ministry_id"), Ministry.name.label("ministry"),
                Function.name.label("function"),
            )
            .select_from(User)
            .outerjoin(MinistryMember, and_(
                MinistryMember.user_id == User.id,
                MinistryMember.deleted_at.is_(None),
                # Registration creates PENDING memberships; retain those selections.
                MinistryMember.status.in_([MembershipStatus.PENDING, MembershipStatus.ACTIVE]),
            ))
            .outerjoin(Ministry, and_(
                Ministry.id == MinistryMember.ministry_id,
                Ministry.status == MinistryStatus.ACTIVE,
                Ministry.deleted_at.is_(None),
            ))
            .outerjoin(MinistryMemberFunction, and_(
                MinistryMemberFunction.ministry_member_id == MinistryMember.id,
                MinistryMemberFunction.revoked_at.is_(None),
            ))
            .outerjoin(Function, and_(
                Function.id == MinistryMemberFunction.function_id,
                Function.ministry_id == Ministry.id,
                Function.status == FunctionStatus.ACTIVE,
                Function.deleted_at.is_(None),
            ))
            .where(User.id == user_id, User.deleted_at.is_(None), User.status == UserStatus.ACTIVE)
            .order_by(Ministry.id, Function.id)
        )
        try:
            return self.db.execute(statement).mappings().all()
        except SQLAlchemyError:
            raise ProfilePersistenceError("Unable to retrieve profile.") from None
