from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.ministry import Ministry
    from app.models.rehearsal_attendance import RehearsalAttendance
    from app.models.user import User


class Rehearsal(Base):
    __tablename__ = "rehearsals"
    __table_args__ = (
        CheckConstraint("end_at > start_at"),
        CheckConstraint("tolerance_minutes >= 0"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ministry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ministries.id"))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tolerance_minutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ministry: Mapped[Ministry] = relationship(back_populates="rehearsals")
    creator: Mapped[User] = relationship(back_populates="created_rehearsals")
    # Let PostgreSQL apply ON DELETE CASCADE, including for loaded attendance.
    rehearsal_attendance: Mapped[list[RehearsalAttendance]] = relationship(
        back_populates="rehearsal", passive_deletes="all",
    )
