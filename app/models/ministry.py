from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Identity, Text, func, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import MinistryStatus

if TYPE_CHECKING:
    from app.models.function import Function
    from app.models.ministry_member import MinistryMember
    from app.models.rehearsal import Rehearsal


class Ministry(Base):
    __tablename__ = "ministries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[MinistryStatus] = mapped_column(
        ENUM(MinistryStatus, name="ministry_status", create_type=False),
        server_default=text("'ACTIVE'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ministry_members: Mapped[list[MinistryMember]] = relationship(back_populates="ministry")
    functions: Mapped[list[Function]] = relationship(back_populates="ministry")
    rehearsals: Mapped[list[Rehearsal]] = relationship(back_populates="ministry")
