from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MembershipStatus, MinistryStatus, RoleScope, RoleStatus, UserStatus
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.ministry_member_role import MinistryMemberRole
from app.models.role import Role
from app.models.user import User


class AuthorizationRepository:
    """Read current privileges without trusting token or request role claims."""

    def __init__(self, db: Session):
        self.db = db

    def has_global_role(self, user_id: int, role_name: str) -> bool:
        assignment = (
            select(MinistryMemberRole.id)
            .join(MinistryMember, MinistryMember.id == MinistryMemberRole.ministry_member_id)
            .join(User, User.id == MinistryMember.user_id)
            .join(Ministry, Ministry.id == MinistryMember.ministry_id)
            .join(Role, Role.id == MinistryMemberRole.role_id)
            .where(
                User.id == user_id, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None),
                MinistryMember.status == MembershipStatus.ACTIVE,
                MinistryMember.deleted_at.is_(None),
                Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None),
                MinistryMemberRole.revoked_at.is_(None),
                Role.name == role_name, Role.scope == RoleScope.GLOBAL,
                Role.status == RoleStatus.ACTIVE, Role.deleted_at.is_(None),
            )
        )
        return bool(self.db.scalar(select(assignment.exists())))
