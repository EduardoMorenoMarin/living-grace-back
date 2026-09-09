from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ministry_member import MinistryMember
    from app.models.role import Role


class MinistryMemberRole(Base):
    __tablename__ = "ministry_member_roles"
    __table_args__ = (
        Index(
            "uq_ministry_member_roles_active",
            "ministry_member_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ministry_member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ministry_members.id"))
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ministry_member: Mapped[MinistryMember] = relationship(back_populates="ministry_member_roles")
    role: Mapped[Role] = relationship(back_populates="ministry_member_roles")
