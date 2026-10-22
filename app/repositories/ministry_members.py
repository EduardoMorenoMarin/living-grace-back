from collections.abc import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import (MembershipNotFound, MembershipTransitionConflict,
                                 MinistryMembersPersistenceError)
from app.models.enums import FunctionStatus, MembershipStatus, MinistryStatus, UserStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_function import MinistryMemberFunction
from app.models.user import User


class MinistryMembersRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_member_rows(self, ministry_id: int, status: MembershipStatus = MembershipStatus.ACTIVE) -> Sequence[RowMapping]:
        eligible = (
            select(
                MinistryMember.id.label("ministry_member_id"), MinistryMember.ministry_id,
                User.id.label("user_id"), User.first_name, User.last_name, MinistryMember.status,
            )
            .join(User, User.id == MinistryMember.user_id)
            .where(
                MinistryMember.ministry_id == ministry_id,
                MinistryMember.status == status,
                MinistryMember.deleted_at.is_(None),
                User.status == UserStatus.ACTIVE, User.deleted_at.is_(None),
            ).subquery()
        )
        statement = (
            select(
                Ministry.id.label("ministry_id"), eligible.c.ministry_member_id,
                eligible.c.user_id, eligible.c.first_name, eligible.c.last_name, eligible.c.status,
                Function.name.label("function"),
            )
            .select_from(Ministry)
            .outerjoin(eligible, eligible.c.ministry_id == Ministry.id)
            .outerjoin(MinistryMemberFunction, and_(
                MinistryMemberFunction.ministry_member_id == eligible.c.ministry_member_id,
                MinistryMemberFunction.revoked_at.is_(None),
            ))
            .outerjoin(Function, and_(
                Function.id == MinistryMemberFunction.function_id,
                Function.ministry_id == Ministry.id,
                Function.status == FunctionStatus.ACTIVE, Function.deleted_at.is_(None),
            ))
            .where(Ministry.id == ministry_id, Ministry.status == MinistryStatus.ACTIVE,
                   Ministry.deleted_at.is_(None))
            .order_by(eligible.c.ministry_member_id, Function.id)
        )
        try:
            return self.db.execute(statement).mappings().all()
        except SQLAlchemyError:
            raise MinistryMembersPersistenceError("Unable to retrieve ministry members.") from None

    def approve(self, ministry_id: int, membership_id: int) -> None:
        try:
            # Lock all eligibility records until commit, including against soft deletion.
            membership = self.db.scalar(
                select(MinistryMember)
                .join(User, User.id == MinistryMember.user_id)
                .join(Ministry, Ministry.id == MinistryMember.ministry_id)
                .where(
                    MinistryMember.id == membership_id,
                    MinistryMember.ministry_id == ministry_id,
                    MinistryMember.deleted_at.is_(None),
                    User.status == UserStatus.ACTIVE, User.deleted_at.is_(None),
                    Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None),
                ).with_for_update().execution_options(populate_existing=True)
            )
            if membership is None:
                raise MembershipNotFound("Membership does not exist or is unavailable.")
            if membership.status != MembershipStatus.PENDING:
                raise MembershipTransitionConflict("Only PENDING memberships can be activated.")
            membership.status = MembershipStatus.ACTIVE
            membership.updated_at = func.now()
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise MinistryMembersPersistenceError("Unable to update membership status.") from None
        except Exception:
            self.db.rollback()
            raise
