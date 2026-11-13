"""Shared SQL eligibility for login, JWTs, and account reconciliation."""
from sqlalchemy import select

from app.models.enums import MembershipStatus, MinistryStatus, UserStatus
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.user import User


def active_membership_exists(user_id):
    return select(MinistryMember.id).join(Ministry, Ministry.id == MinistryMember.ministry_id).where(
        MinistryMember.user_id == user_id,
        MinistryMember.status == MembershipStatus.ACTIVE, MinistryMember.deleted_at.is_(None),
        Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None),
    ).correlate(User).exists()


def eligible_account_conditions():
    return (User.status == UserStatus.ACTIVE, User.deleted_at.is_(None), active_membership_exists(User.id))
