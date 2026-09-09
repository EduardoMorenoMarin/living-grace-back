from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import AttendanceStatus

if TYPE_CHECKING:
    from app.models.ministry_member import MinistryMember
    from app.models.rehearsal import Rehearsal


class RehearsalAttendance(Base):
    __tablename__ = "rehearsal_attendance"
    __table_args__ = (UniqueConstraint("rehearsal_id", "ministry_member_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rehearsal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rehearsals.id", ondelete="CASCADE"),
    )
    ministry_member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ministry_members.id"))
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attendance_status: Mapped[AttendanceStatus | None] = mapped_column(
        ENUM(AttendanceStatus, name="attendance_status", create_type=False),
    )

    rehearsal: Mapped[Rehearsal] = relationship(back_populates="rehearsal_attendance")
    ministry_member: Mapped[MinistryMember] = relationship(back_populates="rehearsal_attendance")
