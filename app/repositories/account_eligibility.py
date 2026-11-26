"""Shared SQL eligibility for login, JWTs, and account reconciliation."""
from sqlalchemy import select

from app.models.enums import MembershipStatus, MinistryStatus, UserStatus
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.models.user import User


def active_ministry_conditions():
    return (Ministry.status == MinistryStatus.ACTIVE, Ministry.deleted_at.is_(None))


def active_membership_conditions():
    return (MinistryMember.status == MembershipStatus.ACTIVE, MinistryMember.deleted_at.is_(None))


def active_user_conditions():
    return (User.status == UserStatus.ACTIVE, User.deleted_at.is_(None))


def active_membership_exists(user_id):
    return select(MinistryMember.id).join(Ministry, Ministry.id == MinistryMember.ministry_id).where(
        MinistryMember.user_id == user_id, *active_membership_conditions(), *active_ministry_conditions(),
    ).correlate(User).exists()


def eligible_account_conditions():
    return (*active_user_conditions(), active_membership_exists(User.id))


def eligible_ministry_member_ids(ministry_id: int):
    return select(MinistryMember.id).join(User, User.id == MinistryMember.user_id).where(
        MinistryMember.ministry_id == ministry_id, *active_membership_conditions(), *active_user_conditions(),
    )
