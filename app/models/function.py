from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import FunctionStatus

if TYPE_CHECKING:
    from app.models.ministry import Ministry
    from app.models.ministry_member_function import MinistryMemberFunction


class Function(Base):
    __tablename__ = "functions"
    __table_args__ = (UniqueConstraint("ministry_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ministry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ministries.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FunctionStatus] = mapped_column(
        ENUM(FunctionStatus, name="function_status", create_type=False),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ministry: Mapped[Ministry] = relationship(back_populates="functions")
    ministry_member_functions: Mapped[list[MinistryMemberFunction]] = relationship(back_populates="function")
