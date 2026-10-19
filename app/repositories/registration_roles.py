import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import RegistrationPersistenceError
from app.models.enums import RoleScope, RoleStatus
from app.models.ministry_member_role import MinistryMemberRole
from app.models.role import Role


class RegistrationRoleRepository:
    """Resolve and persist registration roles in the caller's transaction."""

    def __init__(self, db: Session):
        self.db = db

    def get_user_role_id(self) -> int:
        role_id = self.db.execute(
            select(Role.id).where(
                Role.name == "USER", Role.scope == RoleScope.GLOBAL,
                Role.status == RoleStatus.ACTIVE, Role.deleted_at.is_(None),
            ).with_for_update(read=True)
        ).scalar_one_or_none()
        if role_id is None:
            raise RegistrationPersistenceError("Default registration role is unavailable.")
        return role_id

    def assign(self, role_id: int, membership_ids: list[int]) -> None:
        if not membership_ids:
            return
        assigned = set(self.db.scalars(select(MinistryMemberRole.ministry_member_id).where(
            MinistryMemberRole.ministry_member_id.in_(membership_ids),
            MinistryMemberRole.role_id == role_id,
            MinistryMemberRole.revoked_at.is_(None),
        )))
        self.db.add_all([
            MinistryMemberRole(
                # The verified table has no identity/sequence default. Random positive
                # BIGINTs avoid shared counters; PK collisions fail the transaction.
                id=secrets.randbelow(9223372036854775807) + 1,
                ministry_member_id=membership_id, role_id=role_id,
            )
            for membership_id in dict.fromkeys(membership_ids)
            if membership_id not in assigned
        ])
        try:
            self.db.flush()
        except IntegrityError:
            # A role PK collision is a persistence failure, not an email conflict.
            raise RegistrationPersistenceError("Unable to assign registration roles.") from None
