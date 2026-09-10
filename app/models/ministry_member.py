from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import MembershipStatus

if TYPE_CHECKING:
    from app.models.ministry import Ministry
    from app.models.ministry_member_function import MinistryMemberFunction
    from app.models.ministry_member_role import MinistryMemberRole
    from app.models.rehearsal_attendance import RehearsalAttendance
    from app.models.user import User


class MinistryMember(Base):
    __tablename__ = "ministry_members"
    __table_args__ = (UniqueConstraint("user_id", "ministry_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    ministry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ministries.id"))
    status: Mapped[MembershipStatus] = mapped_column(
        ENUM(MembershipStatus, name="membership_status", create_type=False),
        server_default=text("'PENDING'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="ministry_members")
    ministry: Mapped[Ministry] = relationship(back_populates="ministry_members")
    ministry_member_roles: Mapped[list[MinistryMemberRole]] = relationship(back_populates="ministry_member")
    ministry_member_functions: Mapped[list[MinistryMemberFunction]] = relationship(back_populates="ministry_member")
    rehearsal_attendance: Mapped[list[RehearsalAttendance]] = relationship(back_populates="ministry_member")
