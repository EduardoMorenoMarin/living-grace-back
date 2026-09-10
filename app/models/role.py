from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import RoleScope, RoleStatus

if TYPE_CHECKING:
    from app.models.ministry_member_role import MinistryMemberRole


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", "scope"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[RoleScope] = mapped_column(ENUM(RoleScope, name="role_scope", create_type=False))
    status: Mapped[RoleStatus] = mapped_column(ENUM(RoleStatus, name="role_status", create_type=False))
    is_system_role: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ministry_member_roles: Mapped[list[MinistryMemberRole]] = relationship(back_populates="role")
